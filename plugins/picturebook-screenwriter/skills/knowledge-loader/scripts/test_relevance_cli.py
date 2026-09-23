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

from test_relevance import KEY, answers_for, bundle, evidence, mark_bundle  # noqa: E402


# A 200 body only counts as responded-to when it answers every question the
# batch asked, so the fake answers follow the same candidate chunks the CLI will
# screen. This mirrors `RevisionVectorTests._screen_all` in `test_relevance.py`.
def candidates_of(payload):
    return [chunk for chunk in mark_bundle(payload) if not chunk.required]


def candidates():
    return candidates_of(bundle())


def success_body(answers):
    return json.dumps({"model": "jev-1.13.0", "answers": answers,
                       "usage": {"input_tokens": 120, "output_tokens": 8}})


def screened_body(payload=None):
    payload = bundle() if payload is None else payload
    return TransportResponse(200, success_body(answers_for(candidates_of(payload))), {})


# A page with no hard-constraint section. With every candidate cleared it leaves
# the reduced context entirely, which is what makes the lock bundle's job —
# describing every page that was read — observable from the outside.
SOFT_KEY = "海外绘本/小老鼠迈尔斯/references"

SOFT_BODY = "# 参考资料\n\n## 参考书目\n\n- 《森林的故事》\n"


def two_page_bundle():
    return {
        "items": (evidence(), evidence(key=SOFT_KEY, body=SOFT_BODY)),
        "warnings": (),
        "offline": False,
        "fetched_at": "2026-09-23T10:30:00+08:00",
    }


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
        # The fixture's second page carries no required section, so the reduced
        # context drops the whole page. The lock bundle must not: it describes
        # the knowledge the artifact was built against, and a page that vanished
        # from it would stop the artifact going stale when that page changes.
        payload = two_page_bundle()
        two_page_path = self.run_dir / "two-page-bundle.json"
        two_page_path.write_text(json.dumps(payload, ensure_ascii=False),
                                 encoding="utf-8")
        filtered = self.run_dir / "filtered.json"
        dependency = self.run_dir / "dependency.json"
        code, out, err, _ = self._run(
            ["--bundle", str(two_page_path), "--run-dir", str(self.run_dir),
             "--artifact-type", "script", "--task", "t", "--brief", "b",
             "--filtered-out", str(filtered), "--dependency-out", str(dependency)],
            responses=[screened_body(payload)],
        )
        self.assertEqual(code, 0, err)
        reduced = json.loads(filtered.read_text(encoding="utf-8"))
        self.assertEqual([item["key"] for item in reduced["items"]], [KEY])
        written = json.loads(dependency.read_text(encoding="utf-8"))
        # Both bundles come out of this one run, so the two assertions together
        # pin that the unreduced bundle is the one handed to the lock.
        self.assertEqual([item["key"] for item in written["items"]], [KEY, SOFT_KEY])
        self.assertEqual(written["items"][0]["revision_id"], 17)
        self.assertEqual(written["items"][1]["revision_id"], 17)

    def test_the_report_names_the_run_directory_as_the_run_id(self):
        code, out, _, _ = self._run(
            ["--bundle", str(self.bundle_path), "--run-dir", str(self.run_dir),
             "--artifact-type", "script", "--task", "t", "--brief", "b"],
            responses=[screened_body()],
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["run_id"], self.run_dir.resolve().name)

    def test_an_explicit_run_id_overrides_the_run_directory_name(self):
        code, out, _, _ = self._run(
            ["--bundle", str(self.bundle_path), "--run-dir", str(self.run_dir),
             "--artifact-type", "script", "--task", "t", "--brief", "b",
             "--run-id", "run-01"],
            responses=[screened_body()],
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["run_id"], "run-01")
        # The run id, not the directory, names the on-disk operation directory.
        operation_dirs = [path.name for path in (self.run_dir / "jev").iterdir()]
        self.assertTrue(operation_dirs)
        self.assertTrue(all(name.startswith("run-01-") for name in operation_dirs))

    def test_a_directory_name_that_cannot_name_a_run_is_refused(self):
        # A directory name may carry dots, spaces, or CJK, none of which the
        # request contract accepts as a run id.
        unsafe = self.run_dir / "草稿 run.01"
        unsafe.mkdir()
        argv = ["--bundle", str(self.bundle_path), "--run-dir", str(unsafe),
                "--artifact-type", "script", "--task", "t", "--brief", "b"]
        code, out, err, transport = self._run(argv, responses=[screened_body()])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(json.loads(err)["status"], "error")
        self.assertIn("--run-id", err)
        # The refusal lands before the operation is dispatched, so nothing is paid for.
        self.assertEqual(transport.calls, [])

        code, out, _, _ = self._run(argv + ["--run-id", "draft-01"],
                                    responses=[screened_body()])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["run_id"], "draft-01")

    def test_an_explicit_run_id_is_held_to_the_contract(self):
        code, out, err, _ = self._run(
            ["--bundle", str(self.bundle_path), "--run-dir", str(self.run_dir),
             "--artifact-type", "script", "--task", "t", "--brief", "b",
             "--run-id", "draft 01"],
            responses=[screened_body()],
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("--run-id", json.loads(err)["error"])

    def test_an_unwritable_bundle_path_reports_the_documented_error(self):
        # bundle.json is a file, so this path cannot be created.
        blocked = self.bundle_path / "filtered.json"
        code, out, err, transport = self._run(
            ["--bundle", str(self.bundle_path), "--run-dir", str(self.run_dir),
             "--artifact-type", "script", "--task", "t", "--brief", "b",
             "--filtered-out", str(blocked)],
            responses=[screened_body()],
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(json.loads(err)["status"], "error")
        # The screening call had already been made: the failure is reported
        # instead of the report being lost to a traceback.
        self.assertEqual(len(transport.calls), 1)

    def test_a_bundle_that_cannot_be_read_is_refused_before_anything_is_paid(self):
        # None of these shapes is an evidence bundle, and each one used to
        # escape the report: with an output flag set the dependency bundle
        # raised `'list' object has no attribute 'items'`, and without one the
        # CLI exited 0 on a screen of nothing. The last seven are the fields the
        # CLI reads besides `items[].key` / `items[].content`: `warnings` fed
        # straight into `list()` raised `TypeError` after the paid call, a
        # numeric `source_revisions` raised before dispatch, and a bare
        # warning string was silently split into one warning per character. Two
        # of the seven are shapes only this guard refuses — a present `null`
        # warnings field defeats the readers' `get(key, default)` fallback and
        # raised after the paid call, and a `source_revisions` list of pairs is
        # accepted by `dict()` and by the request contract, so nothing
        # downstream would have refused it; the rest it refuses as well, they
        # just have a second net further in. Every sub-case writes to its own
        # pair of paths, so one regression cannot cascade into the assertions
        # of the shapes after it.
        complete = evidence()
        shapes = {
            "a list at the top level": [complete],
            "an item that is not an object": {"items": [None]},
            "an items value that is not a list": {"items": "abc"},
            "an item with no key":
                {"items": [{k: v for k, v in complete.items() if k != "key"}]},
            "an item with no content":
                {"items": [{k: v for k, v in complete.items() if k != "content"}]},
            "an item whose content is not text":
                {"items": [{**complete, "content": 5}]},
            "a warnings value that is not a list":
                {"items": [complete], "warnings": 5},
            "a warnings value that is null":
                {"items": [complete], "warnings": None},
            "a warnings value that is a bare string":
                {"items": [complete], "warnings": "索引尚未同步"},
            "a warnings list that is not all text":
                {"items": [complete], "warnings": ["索引尚未同步", 5]},
            "a source_revisions value that is not an object":
                {"items": [{**complete, "source_revisions": 5}]},
            "a source_revisions value that is a list of pairs":
                {"items": [{**complete, "source_revisions": [["node-a", "17"]]}]},
            "a source_revisions value that is a bare string":
                {"items": [{**complete, "source_revisions": "node-a=17"}]},
            "source_revisions whose values are not text":
                {"items": [{**complete, "source_revisions": {"node-a": 17}}]},
        }
        for position, (name, payload) in enumerate(shapes.items()):
            with self.subTest(shape=name):
                path = self.run_dir / f"bad-bundle-{position}.json"
                path.write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                )
                filtered = self.run_dir / f"filtered-{position}.json"
                dependency = self.run_dir / f"dependency-{position}.json"
                code, out, err, transport = self._run(
                    ["--bundle", str(path), "--run-dir", str(self.run_dir),
                     "--artifact-type", "script", "--task", "t", "--brief", "b",
                     "--filtered-out", str(filtered),
                     "--dependency-out", str(dependency)],
                    responses=[screened_body()],
                )
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertEqual(json.loads(err)["status"], "error")
                self.assertEqual(transport.calls, [])
                self.assertFalse(filtered.exists())
                self.assertFalse(dependency.exists())

    def test_a_bundle_with_warnings_and_a_revision_vector_still_screens(self):
        # The guard is about shape, not about content: the loader's own
        # warnings and version vector must survive, and the warning the CLI
        # appends joins them instead of replacing them.
        path = self.run_dir / "warned-bundle.json"
        payload = {**bundle(), "warnings": ["索引尚未同步"]}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        written = self.run_dir / "filtered.json"
        code, out, err, transport = self._run(
            ["--bundle", str(path), "--run-dir", str(self.run_dir),
             "--artifact-type", "script", "--task", "t", "--brief", "b",
             "--filtered-out", str(written)],
            responses=[screened_body()],
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(json.loads(out)["results"][0]["status"], "succeeded")
        filtered = json.loads(written.read_text(encoding="utf-8"))
        self.assertIn("索引尚未同步", filtered["warnings"])
        self.assertEqual(
            filtered["items"][0]["source_revisions"], {"node-a": "17"}
        )

    def test_a_bundle_with_no_pages_is_screened_as_nothing(self):
        # The refusal is about a file that cannot be read as a bundle, not about
        # a bundle that carries no pages: an authority read that matched nothing
        # is reported honestly, and still sends no request.
        path = self.run_dir / "empty-bundle.json"
        path.write_text(
            json.dumps({"items": [], "warnings": [], "offline": False,
                        "fetched_at": "2026-09-23T10:30:00+08:00"},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        code, out, _, transport = self._run(
            ["--bundle", str(path), "--run-dir", str(self.run_dir),
             "--artifact-type", "script", "--task", "t", "--brief", "b"],
            responses=[screened_body()],
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["required_count"], 0)
        self.assertEqual(payload["candidate_count"], 0)
        self.assertEqual(payload["results"], [])
        self.assertEqual(transport.calls, [])

    def test_usage_errors_exit_two(self):
        code, _, _, _ = self._run([])
        self.assertEqual(code, 2)

    def test_a_stale_record_is_reported_by_name_and_costs_nothing(self):
        # `result.json` is only rewritten by a dispatch that succeeds, so an
        # edit to the page plus one failed dispatch leaves the edited request
        # beside the earlier answer. The report has to name the record that
        # stopped the batch, or the caller sees an empty, successful-looking
        # screen on every re-run and has nothing to act on.
        argv = ["--bundle", str(self.bundle_path), "--run-dir", str(self.run_dir),
                "--artifact-type", "script", "--task", "t", "--brief", "b"]
        code, _, _, first = self._run(argv, responses=[screened_body()])
        self.assertEqual(code, 0)
        self.assertEqual(len(first.calls), 1)

        edited = bundle()
        edited["items"][0]["content"] += "\n## 新增段落\n\n编辑后新增的一句话。\n"
        self.bundle_path.write_text(
            json.dumps(edited, ensure_ascii=False), encoding="utf-8"
        )
        code, out, _, failed = self._run(
            argv, responses=[TransportResponse(500, "boom", {})]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["results"][0]["status"], "failed")
        self.assertEqual(len(failed.calls), 1)

        filtered = self.run_dir / "filtered.json"
        code, out, err, third = self._run(argv + ["--filtered-out", str(filtered)])
        self.assertEqual(code, 0, err)
        self.assertEqual(third.calls, [])

        payload = json.loads(out)
        self.assertEqual(payload["results"], [])
        self.assertEqual(payload["excluded_soft"], [])
        self.assertEqual(len(payload["blocked_records"]), 1)
        blocked = payload["blocked_records"][0]
        self.assertEqual(blocked["reason"], "stale_result")
        self.assertTrue(
            blocked["operation_id"].endswith(f"-{payload['operation']}-batch-001")
        )
        self.assertTrue(blocked["path"].endswith("result.json"))
        # Every chunk stays in the model context: nothing was excluded on
        # evidence this run could not vouch for.
        self.assertTrue(payload["uncertain_chunk_ids"])
        self.assertTrue(
            set(payload["uncertain_chunk_ids"]) <= set(payload["kept_chunk_ids"])
        )
        self.assertTrue(
            json.loads(filtered.read_text(encoding="utf-8"))["items"]
        )
        self.assertTrue(
            any(
                "blocked_records" in warning
                for warning in json.loads(
                    filtered.read_text(encoding="utf-8")
                )["warnings"]
            )
        )


if __name__ == "__main__":
    unittest.main()
