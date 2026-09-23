import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

# The CLI composes the knowledge side with the shared decision runtime, so both
# script directories join the import path here, exactly as `test_relevance.py`
# does for the operation the CLI wraps.
RUNTIME_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "jev-decision-runtime" / "scripts"
)
if str(RUNTIME_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SCRIPTS))

from jev_client import API_KEY_ENV, FakeTransport, TransportResponse  # noqa: E402
from relevance_cli import main  # noqa: E402

from test_relevance import KEY, answers_for, bundle, mark_bundle  # noqa: E402


# A 200 body only counts as responded-to when it answers every question the
# batch asked, so the fake answers follow the same candidate chunks the CLI will
# screen. This mirrors `RevisionVectorTests._screen_all` in `test_relevance.py`.
def candidates():
    return [chunk for chunk in mark_bundle(bundle()) if not chunk.required]


def success_body(answers):
    return json.dumps({"model": "jev-1.13.0", "answers": answers,
                       "usage": {"input_tokens": 120, "output_tokens": 8}})


def screened_body():
    return TransportResponse(200, success_body(answers_for(candidates())), {})


class RelevanceCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)
        self.bundle_path = self.run_dir / "bundle.json"
        self.bundle_path.write_text(json.dumps(bundle(), ensure_ascii=False),
                                    encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, argv, responses=None, environ=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        transport = FakeTransport(responses=responses or [])
        code = main(
            argv, environ={API_KEY_ENV: "sk-abc"} if environ is None else environ,
            transport_factory=lambda: transport, stdout=stdout, stderr=stderr,
        )
        return code, stdout.getvalue(), stderr.getvalue(), transport

    def test_credentials_are_refused_before_parsing(self):
        code, out, err, _ = self._run(
            ["--bundle", str(self.bundle_path), "--run-dir", str(self.run_dir),
             "--artifact-type", "script", "--task", "t", "--brief", "b",
             "--api-key=SUPER-SECRET"]
        )
        self.assertEqual(code, 2)
        self.assertNotIn("SUPER-SECRET", err)
        self.assertNotIn("SUPER-SECRET", out)

    def test_a_missing_key_still_reports_the_kept_context(self):
        code, out, _, transport = self._run(
            ["--bundle", str(self.bundle_path), "--run-dir", str(self.run_dir),
             "--artifact-type", "script", "--task", "t", "--brief", "b"],
            environ={},
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["results"][0]["status"], "waiting_for_jev_key")
        self.assertEqual(transport.calls, [])
        self.assertTrue(payload["required_count"] >= 1)

    def test_a_successful_screen_writes_both_bundles(self):
        filtered = self.run_dir / "filtered.json"
        dependency = self.run_dir / "dependency.json"
        code, out, _, _ = self._run(
            ["--bundle", str(self.bundle_path), "--run-dir", str(self.run_dir),
             "--artifact-type", "script", "--task", "t", "--brief", "b",
             "--filtered-out", str(filtered), "--dependency-out", str(dependency)],
            responses=[screened_body()],
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["operation"], "knowledge_relevance")
        self.assertEqual(payload["results"][0]["status"], "succeeded")
        self.assertTrue(json.loads(filtered.read_text(encoding="utf-8"))["items"])

    def test_the_dependency_bundle_keeps_every_page_even_when_filtered_loses_some(self):
        filtered = self.run_dir / "filtered.json"
        dependency = self.run_dir / "dependency.json"
        self._run(
            ["--bundle", str(self.bundle_path), "--run-dir", str(self.run_dir),
             "--artifact-type", "script", "--task", "t", "--brief", "b",
             "--filtered-out", str(filtered), "--dependency-out", str(dependency)],
            responses=[screened_body()],
        )
        written = json.loads(dependency.read_text(encoding="utf-8"))
        self.assertEqual([item["key"] for item in written["items"]], [KEY])
        self.assertEqual(written["items"][0]["revision_id"], 17)

    def test_the_report_names_the_run_directory_as_the_run_id(self):
        code, out, _, _ = self._run(
            ["--bundle", str(self.bundle_path), "--run-dir", str(self.run_dir),
             "--artifact-type", "script", "--task", "t", "--brief", "b"],
            responses=[screened_body()],
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["run_id"], self.run_dir.resolve().name)

    def test_usage_errors_exit_two(self):
        code, _, _, _ = self._run([])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
