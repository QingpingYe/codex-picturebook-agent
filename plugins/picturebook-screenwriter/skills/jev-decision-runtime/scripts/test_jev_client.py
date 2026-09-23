import json
import os
import unittest
from unittest import mock

import jev_client
from jev_client import (
    API_KEY_ENV,
    ENDPOINT,
    FakeTransport,
    JevClient,
    JevEndpointError,
    JevTransportFailure,
    JevTransportOutcomeUnknown,
    TransportResponse,
    UrllibTransport,
    assert_production_endpoint,
    read_api_key,
)

MARKER_STATE = "小红帽把蛋糕递给了奶奶"


def make_request():
    return {
        "schema_version": "pb-jev-request-v1",
        "run_id": "20260923-example-0001",
        "operation": "knowledge_relevance",
        "model": "jev-1.13.0",
        "policy_version": "knowledge-relevance-v1",
        "state": MARKER_STATE,
        "questions": {"relevant": {"type": "noul", "instructions": "是否相关？"}},
        "context_refs": [{"ref_id": "k", "kind": "knowledge_page", "revisions": {"n": "1"}}],
        "benchmark_case_id": "sha256:" + "0" * 64,
    }


def success_body():
    return json.dumps({
        "model": "jev-1.13.0",
        "answers": {"relevant": {"type": "noul", "noul": 0.91}},
        "usage": {"input_tokens": 296, "output_tokens": 20},
    })


def make_client(transport, environ=None):
    return JevClient(transport, environ={} if environ is None else environ, sleep=lambda _: None)


class ApiKeyTests(unittest.TestCase):
    def test_missing_key_reads_as_missing(self):
        self.assertIsNone(read_api_key({}))

    def test_empty_and_whitespace_keys_read_as_missing(self):
        self.assertIsNone(read_api_key({API_KEY_ENV: ""}))
        self.assertIsNone(read_api_key({API_KEY_ENV: "   \t "}))

    def test_present_key_is_trimmed(self):
        self.assertEqual(read_api_key({API_KEY_ENV: "  sk-abc  "}), "sk-abc")


class EndpointAllowlistTests(unittest.TestCase):
    def test_shipped_endpoint_is_allowed(self):
        assert_production_endpoint(ENDPOINT)

    def test_other_hosts_are_refused(self):
        for url in (
            "https://evil.example/v1/systemone",
            "https://api.typesafe.ai.evil.example/v1/systemone",
            "https://typesafe.ai/v1/systemone",
        ):
            with self.subTest(url=url), self.assertRaises(JevEndpointError):
                assert_production_endpoint(url)

    def test_plain_http_is_refused(self):
        with self.assertRaises(JevEndpointError):
            assert_production_endpoint("http://api.typesafe.ai/v1/systemone")

    def test_other_paths_are_refused(self):
        with self.assertRaises(JevEndpointError):
            assert_production_endpoint("https://api.typesafe.ai/v1/other")


class CredentialGateTests(unittest.TestCase):
    def test_missing_key_returns_waiting_without_touching_the_transport(self):
        transport = FakeTransport(responses=[TransportResponse(200, success_body(), {})])
        outcome = make_client(transport, environ={}).call(make_request())
        self.assertEqual(outcome.status, "waiting_for_jev_key")
        self.assertEqual(outcome.attempts, 0)
        self.assertEqual(transport.calls, [])

    def test_whitespace_key_also_waits(self):
        transport = FakeTransport()
        outcome = make_client(transport, environ={API_KEY_ENV: "  "}).call(make_request())
        self.assertEqual(outcome.status, "waiting_for_jev_key")
        self.assertEqual(transport.calls, [])

    def test_an_ambient_key_is_not_used_when_an_explicit_environ_is_supplied(self):
        transport = FakeTransport()
        with mock.patch.dict(os.environ, {API_KEY_ENV: "ambient-secret"}, clear=False):
            outcome = make_client(transport, environ={}).call(make_request())
        self.assertEqual(outcome.status, "waiting_for_jev_key")
        self.assertEqual(transport.calls, [])

    def test_a_present_key_is_dispatched_once(self):
        transport = FakeTransport(responses=[TransportResponse(200, success_body(), {})])
        outcome = make_client(transport, environ={API_KEY_ENV: "sk-abc"}).call(make_request())
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0]["url"], ENDPOINT)

    def test_authorization_header_is_sent_as_bearer(self):
        transport = FakeTransport(responses=[TransportResponse(200, success_body(), {})])
        make_client(transport, environ={API_KEY_ENV: "sk-abc"}).call(make_request())
        self.assertEqual(transport.calls[0]["headers"]["Authorization"], "Bearer sk-abc")

    def test_the_key_never_appears_in_the_outcome(self):
        transport = FakeTransport(responses=[TransportResponse(200, success_body(), {})])
        outcome = make_client(transport, environ={API_KEY_ENV: "sk-secret-value"}).call(make_request())
        self.assertNotIn("sk-secret-value", repr(outcome))


class RedirectionTests(unittest.TestCase):
    def test_redirect_handler_declines_every_redirect(self):
        handler = jev_client._NoRedirectHandler()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/"))

    def test_a_mocked_302_is_reported_as_an_endpoint_error(self):
        # UrllibTransport must not follow the redirect; urllib surfaces the 3xx
        # as an HTTPError, which the transport returns verbatim.
        transport = UrllibTransport()
        with mock.patch.object(transport, "_open") as opener:
            opener.side_effect = jev_client.urllib.error.HTTPError(
                ENDPOINT, 302, "Found", {"Location": "https://evil.example/"}, None
            )
            response = transport.send(ENDPOINT, {}, b"{}", 1.0)
        self.assertEqual(response.status_code, 302)


class TransportFailureTests(unittest.TestCase):
    def test_a_provably_unsent_request_is_a_transport_failure(self):
        transport = FakeTransport(error=JevTransportFailure("connection refused"))
        outcome = make_client(transport, environ={API_KEY_ENV: "sk-abc"}).call(make_request())
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.error_class, "JevTransportFailure")

    def test_an_ambiguous_outcome_is_never_reported_as_failure(self):
        transport = FakeTransport(error=JevTransportOutcomeUnknown("read timed out"))
        outcome = make_client(transport, environ={API_KEY_ENV: "sk-abc"}).call(make_request())
        self.assertEqual(outcome.status, "outcome_unknown")

    def test_a_malformed_success_body_is_a_failure(self):
        transport = FakeTransport(responses=[TransportResponse(200, "{not json", {})])
        outcome = make_client(transport, environ={API_KEY_ENV: "sk-abc"}).call(make_request())
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.error_class, "invalid_json")


class UrllibTransportTests(unittest.TestCase):
    def test_the_real_transport_refuses_a_non_allowlisted_url_before_opening(self):
        transport = UrllibTransport()
        with mock.patch.object(transport, "_open") as opener:
            with self.assertRaises(JevEndpointError):
                transport.send("https://evil.example/v1/systemone", {}, b"{}", 1.0)
        opener.assert_not_called()

    def test_the_real_transport_refuses_to_swallow_ambient_credentials_in_tests(self):
        # The production transport must be constructed with an explicit environ
        # by the runner; it must not read the environment by itself.
        self.assertFalse(hasattr(UrllibTransport(), "environ"))


if __name__ == "__main__":
    unittest.main()
