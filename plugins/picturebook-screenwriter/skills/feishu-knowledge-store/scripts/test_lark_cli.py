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
    def test_update_uses_current_revision_as_precondition(self):
        runner = FakeRunner(ok({"data": {"document": {"revision_id": 13}}}))
        client = LarkCli(Path("lark-cli"), "user", runner)
        self.assertEqual(client.update_doc("doccn1", 12, "# 更新"), 13)
        self.assertIn("--revision-id", runner.calls[-1])
        self.assertIn("12", runner.calls[-1])

    def test_conflict_response_becomes_revision_conflict(self):
        client = LarkCli(Path("lark-cli"), "user", FakeRunner(revision_conflict()))
        with self.assertRaises(RevisionConflict):
            client.update_doc("doccn1", 12, "# 更新")

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
