import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lark_cli import AuthenticationError, LarkCli, RevisionConflict


class Completed:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class FakeRunner:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, command, **_kwargs):
        self.calls.append(command)
        return self.responses.pop(0)


def ok(payload):
    return Completed(json.dumps(payload))


def revision_conflict():
    return Completed('{"code": 1770001, "msg": "revision conflict"}', returncode=1)


class LarkCliTests(unittest.TestCase):
    def test_notice_is_not_used_as_business_data(self):
        business = {
            "code": 0,
            "data": {"items": [{"token": "A"}, {"token": "B"}, {"token": "C"}]},
            "_notice": {"update": {"current": "1.0.95", "latest": "1.0.96"}},
        }
        stdout = json.dumps(business)
        result = LarkCli._final_json(stdout)
        self.assertEqual(result["data"]["items"][0]["token"], "A")
        self.assertIn("_notice", result)

    def test_fetch_doc_uses_fetch_and_not_get(self):
        runner = FakeRunner(ok({"data": {"document": {"content": "# 正文"}}}))
        client = LarkCli(Path("lark-cli"), "user", runner)
        client.fetch_doc("doccn1")
        self.assertIn("+fetch", runner.calls[-1])
        self.assertNotIn("+get", runner.calls[-1])

    def test_list_nodes_requires_space_id_and_uses_pagination(self):
        runner = FakeRunner(ok({"data": {"items": [{"title": "节点"}]}}))
        client = LarkCli(Path("lark-cli"), "user", runner)
        nodes = client.list_nodes("space-1", "node-1")
        self.assertEqual(nodes, [{"title": "节点"}])
        self.assertIn("--space-id", runner.calls[-1])
        self.assertIn("space-1", runner.calls[-1])
        self.assertIn("--page-all", runner.calls[-1])

    def test_update_doc_uses_temp_file_for_long_content(self):
        runner = FakeRunner(ok({"data": {"document": {"revision_id": 13}}}))
        client = LarkCli(Path("lark-cli"), "user", runner)
        result = client.update_doc("doccn1", 12, "# 正文" * 500)
        self.assertIn("--revision-id", runner.calls[-1])
        content_arg = runner.calls[-1][runner.calls[-1].index("--content") + 1]
        self.assertTrue(content_arg.startswith("@"))
        self.assertFalse(Path(content_arg[1:]).exists())

    def test_supported_version_is_detected(self):
        runner = FakeRunner(Completed("lark-cli version 1.0.95\n"))
        client = LarkCli(Path("lark-cli"), "user", runner)
        self.assertEqual(client.verify_supported_version(), {"version": "1.0.95"})

    def test_final_json_prefers_top_level_payload_when_notice_is_a_separate_line(self):
        business = {"code": 0, "data": {"items": [{"token": "A"}]}}
        notice = {"_notice": {"update": {"current": "1.0.95", "latest": "1.0.96"}}}
        stdout = json.dumps(business) + "\n" + json.dumps(notice) + "\n"
        result = LarkCli._final_json(stdout)
        self.assertEqual(result["data"]["items"][0]["token"], "A")

    def test_update_uses_current_revision_as_precondition(self):
        runner = FakeRunner(ok({"data": {"document": {"revision_id": 13}}}))
        client = LarkCli(Path("lark-cli"), "user", runner)
        self.assertEqual(client.update_doc("doccn1", 12, "# 更新"), {"data": {"document": {"revision_id": 13}}})
        self.assertIn("--revision-id", runner.calls[-1])
        self.assertIn("12", runner.calls[-1])

    def test_update_returns_the_full_result_for_warning_inspection(self):
        payload = {"code": 0, "data": {"result": "partial_success", "document": {"revision_id": 14}},
                   "warnings": [{"msg": "partial update"}]}
        runner = FakeRunner(ok(payload))
        client = LarkCli(Path("lark-cli"), "user", runner)
        self.assertEqual(client.update_doc("doccn1", 13, "# 更新"), payload)

    def test_conflict_response_becomes_revision_conflict(self):
        client = LarkCli(Path("lark-cli"), "user", FakeRunner(revision_conflict()))
        with self.assertRaises(RevisionConflict):
            client.update_doc("doccn1", 12, "# 更新")

    def test_document_reads_use_fetch_command(self):
        runner = FakeRunner(
            ok({"data": {"document": {"content": "# 当前版"}}}),
            ok({"data": {"document": {"content": "# 历史版"}}}),
        )
        client = LarkCli(Path("lark-cli"), "user", runner)
        client.fetch_doc("doccn1")
        client.fetch_doc_revision("doccn1", 12)
        for call in runner.calls:
            self.assertIn("+fetch", call)
            self.assertNotIn("+get", call)
        self.assertIn("--doc-format", runner.calls[0])
        self.assertIn("markdown", runner.calls[0])
        self.assertIn("--revision-id", runner.calls[1])
        self.assertIn("12", runner.calls[1])

    def test_preflight_is_read_only_and_checks_auth_then_root(self):
        runner = FakeRunner(ok({"data": {"user": "me"}}), ok({"data": {"node": {"token": "root"}}}))
        client = LarkCli(Path("lark-cli"), "user", runner)
        client.preflight("root")
        self.assertEqual(runner.calls[0][1:], ["auth", "status", "--json", "--verify"])
        self.assertEqual(runner.calls[1][1:3], ["wiki", "+node-get"])
        self.assertFalse(any("+create" in call or "+update" in call for call in runner.calls))

    def test_error_messages_do_not_expose_token_like_values(self):
        runner = FakeRunner(Completed("error access_token=super-secret-token-value", returncode=1))
        with self.assertRaises(AuthenticationError) as raised:
            LarkCli(Path("lark-cli"), "user", runner).get_node("doccnSensitiveToken")
        self.assertNotIn("super-secret-token-value", str(raised.exception))

    def test_error_messages_redact_json_token_fields(self):
        runner = FakeRunner(Completed('{"code": 401, "token": "short-secret"}', returncode=1))
        with self.assertRaises(AuthenticationError) as raised:
            LarkCli(Path("lark-cli"), "user", runner).get_node("doccnSensitiveToken")
        self.assertNotIn("short-secret", str(raised.exception))

    def test_error_messages_redact_bearer_credentials_regardless_of_length(self):
        runner = FakeRunner(Completed("Authorization: Bearer shortsecret", returncode=1))
        with self.assertRaises(AuthenticationError) as raised:
            LarkCli(Path("lark-cli"), "user", runner).get_node("doccnSensitiveToken")
        self.assertNotIn("shortsecret", str(raised.exception))

    def test_final_json_accepts_diagnostics_before_pretty_printed_payload(self):
        stdout = "diagnostic line\n{\n  \"data\": {\n    \"node\": {\"token\": \"root\"}\n  }\n}\n"
        result = LarkCli(Path("lark-cli"), "user", FakeRunner(Completed(stdout))).get_node("root")
        self.assertEqual(result["data"]["node"]["token"], "root")


if __name__ == "__main__":
    unittest.main()
