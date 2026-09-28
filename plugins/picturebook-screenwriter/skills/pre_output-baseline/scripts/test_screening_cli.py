import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

# The CLI composes the pre-output side with the knowledge side and the shared
# decision runtime, so the same script directories the CLI joins join the import
# path here, exactly as `test_screening_runner.py` does for the operation it
# wraps.
RUNTIME_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "jev-decision-runtime" / "scripts"
)
if str(RUNTIME_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SCRIPTS))

from jev_client import API_KEY_ENV, FakeTransport, TransportResponse  # noqa: E402
from decision_contract import default_policy_path  # noqa: E402
from redline_catalog import catalog_from_bundle  # noqa: E402
from screening_runner import screening_items  # noqa: E402
from screening_cli import main  # noqa: E402

from test_screening_runner import PAGES, SCRIPT, bundle  # noqa: E402

# A draft that literally matches the catalog's single rule. The literal scan is
# a candidate signal, so this is what a proxy conflict is made of.
LITERAL_SCRIPT = SCRIPT.replace('| 3 | 3 | Miles rolled', '| 3 | 3 | 变勇敢了 / Miles rolled')


def noul(value):
    return {"type": "noul", "noul": value}


def answers_for(rules, *, value=0.05, overrides=None):
    """Exactly the asked question set for one catalog, every answer clear.

    The response contract rejects an answer for a question that was not asked,
    so this has to mirror `screening_items` precisely: the catalog the CLI
    rebuilds and this test rebuilds are the same function.
    """

    answers = {}
    for item in screening_items(PAGES, rules):
        for dimension in item["dimensions"]:
            answers[f"{item['item_id']}::{dimension}"] = noul(value)
    for question_id, answer in (overrides or {}).items():
        answers[question_id] = noul(answer)
    return answers


def success_body(answers):
    return json.dumps({"model": "jev-1.13.0", "answers": answers,
                       "usage": {"input_tokens": 400, "output_tokens": 40}})


def redline_question_id(rules, item_id="page-1", pattern="变勇敢了"):
    """Build one red-line question id from the catalog's own rule id."""

    rule = next(rule for rule in rules if rule.pattern == pattern)
    return f"{item_id}::redline:{rule.rule_id}"


class ScreeningCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self._tmp.name)
        self.script_path = self.run_dir / "script.md"
        self.script_path.write_text(SCRIPT, encoding="utf-8")
        self.bundle_path = self.run_dir / "bundle.json"
        self._write_bundle(bundle(), self.bundle_path)

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _write_bundle(payload, path):
        Path(path).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _argv(self, **overrides):
        values = {
            "script": str(self.script_path), "bundle": str(self.bundle_path),
            "run_dir": str(self.run_dir),
        }
        values.update(overrides)
        argv = []
        for name, value in values.items():
            if value is None:
                continue
            argv.extend([f"--{name.replace('_', '-')}", str(value)])
        return argv

    def _run(self, argv, responses=None, environ=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        transport = FakeTransport(responses=responses or [])
        code = main(
            argv, environ={API_KEY_ENV: "sk-abc"} if environ is None else environ,
            transport_factory=lambda: transport, stdout=stdout, stderr=stderr,
        )
        return code, stdout.getvalue(), stderr.getvalue(), transport

    def _clear_screen(self, **overrides):
        """A screen whose only call answers every asked question in the clear band."""

        rules = catalog_from_bundle(bundle())
        argv = self._argv(**overrides)
        return self._run(argv, responses=[
            TransportResponse(200, success_body(answers_for(rules)), {})
        ])

    def test_credentials_are_refused_before_parsing(self):
        code, out, err, _ = self._run(self._argv() + ["--api-key=SUPER-SECRET"])
        self.assertEqual(code, 2)
        self.assertNotIn("SUPER-SECRET", err)
        self.assertNotIn("SUPER-SECRET", out)

    def test_a_missing_key_still_reports_the_screen_and_never_clears(self):
        code, out, _, transport = self._run(self._argv(), environ={})
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["results"][0]["status"], "waiting_for_jev_key")
        self.assertEqual(transport.calls, [])
        self.assertEqual(payload["summary"]["runtime_failure"], payload["summary"]["total"])
        self.assertEqual(payload["summary"]["screened_clear"], 0)
        self.assertTrue(payload["escalation_package"])

    def test_a_successful_screen_reports_the_ratios_it_cleared(self):
        code, out, err, transport = self._clear_screen()
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["schema_version"], "pb-quality-prefilter-report-v1")
        self.assertEqual(payload["operation"], "text_quality_prefilter")
        self.assertEqual(payload["results"][0]["status"], "succeeded")
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(payload["summary"]["screened_clear"], payload["summary"]["total"])
        self.assertEqual(payload["summary"]["screened_clear_ratio"], 1.0)
        self.assertEqual(payload["summary"]["escalation_ratio"], 0.0)
        self.assertEqual(payload["escalation_package"], [])
        self.assertEqual(payload["catalog_size"], 1)
        self.assertFalse(payload["catalog_gap"])

    def test_the_asked_redline_dimensions_come_from_the_authority_catalog(self):
        # The dimensions are built from the catalog rather than from the literal
        # scan, so a red line the scan missed is still asked about: the run has
        # to carry a question per active red line, and the id it uses is the
        # catalog's own.
        rules = catalog_from_bundle(bundle())
        asked = {
            f"{item['item_id']}::{dimension}"
            for item in screening_items(PAGES, rules)
            for dimension in item["dimensions"]
        }
        code, out, err, _ = self._clear_screen()
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        reported = {route["item_id"] for route in payload["routes"]}
        redline = {name for name in asked if "::redline:" in name}
        self.assertTrue(redline)
        self.assertTrue(redline <= reported)
        self.assertEqual(payload["catalog_size"], 1)
        self.assertTrue(redline_question_id(rules) in asked)

    def test_a_risky_dimension_reaches_the_escalation_package(self):
        rules = catalog_from_bundle(bundle())
        answers = answers_for(rules)
        answers["page-2::direct_moralizing"] = noul(0.97)
        escalation = self.run_dir / "escalation.json"
        code, out, err, _ = self._run(
            self._argv(escalation_out=str(escalation)),
            responses=[TransportResponse(200, success_body(answers), {})],
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["summary"]["escalated"], 1)
        written = json.loads(escalation.read_text(encoding="utf-8"))
        self.assertEqual(written["schema_version"], "pb-quality-escalation-package-v1")
        entry = next(item for item in written["items"]
                     if item["item_id"] == "page-2::direct_moralizing")
        self.assertEqual(entry["outcome"], "escalate_llm")
        self.assertIn("Mine!", entry["evidence"])
        # Only the risky dimension is escalated: the pre-screen is per dimension.
        self.assertEqual([item["item_id"] for item in written["items"]],
                         ["page-2::direct_moralizing"])

    def test_the_report_records_the_page_and_item_counts(self):
        code, out, err, _ = self._clear_screen()
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["page_count"], len(PAGES))
        self.assertEqual(payload["item_count"], len(PAGES))
        self.assertEqual(payload["age_band"], "")
        self.assertTrue(any("--age-band" in note for note in payload["warnings"]))

    def test_the_report_names_the_run_directory_as_the_run_id(self):
        code, out, err, _ = self._clear_screen()
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["run_id"], self.run_dir.resolve().name)

    def test_an_explicit_run_id_overrides_the_run_directory_name(self):
        code, out, err, _ = self._clear_screen(run_id="run-01")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["run_id"], "run-01")
        # The run id, not the directory, names the on-disk operation directory.
        operation_dirs = [path.name for path in (self.run_dir / "jev").iterdir()]
        self.assertTrue(operation_dirs)
        self.assertTrue(all(name.startswith("run-01-") for name in operation_dirs))

    def test_a_directory_name_that_cannot_name_a_run_is_refused(self):
        unsafe = self.run_dir / "草稿 run.01"
        unsafe.mkdir()
        argv = self._argv(run_dir=str(unsafe))
        rules = catalog_from_bundle(bundle())
        responses = [TransportResponse(200, success_body(answers_for(rules)), {})]
        code, out, err, transport = self._run(argv, responses=responses)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(json.loads(err)["status"], "error")
        self.assertIn("--run-id", err)
        # The refusal lands before the operation is dispatched, so nothing is paid for.
        self.assertEqual(transport.calls, [])

        code, out, _, _ = self._run(
            argv + ["--run-id", "draft-01"], responses=responses
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["run_id"], "draft-01")

    def test_an_explicit_run_id_is_held_to_the_contract(self):
        code, out, err, _ = self._clear_screen(run_id="draft 01")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("--run-id", json.loads(err)["error"])

    def test_a_window_width_below_one_is_refused(self):
        code, out, err, transport = self._run(self._argv(window_width=0))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("--window-width", json.loads(err)["error"])
        self.assertEqual(transport.calls, [])

    def test_usage_errors_exit_two(self):
        code, _, _, _ = self._run([])
        self.assertEqual(code, 2)

    def test_an_unwritable_report_path_reports_the_documented_error(self):
        # bundle.json is a file, so this path cannot be created.
        blocked = self.bundle_path / "report.json"
        code, out, err, transport = self._clear_screen(report_out=str(blocked))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(json.loads(err)["status"], "error")
        # The screening call had already been made: the failure is reported
        # instead of the report being lost to a traceback.
        self.assertEqual(len(transport.calls), 1)

    def test_a_bundle_that_cannot_be_read_is_refused_before_anything_is_paid(self):
        # None of these shapes is an evidence bundle. The first four used to
        # escape as an `AttributeError`/`TypeError` traceback on the way to the
        # catalog, and the last two are the fields whose readers default only a
        # missing key: a present `null` warnings field and a `source_revisions`
        # list of pairs are accepted by nothing downstream either.
        complete = bundle()["items"][0]
        shapes = {
            "a list at the top level": [complete],
            "an item that is not an object": {"items": [None]},
            "an items value that is not a list": {"items": "abc"},
            "an item with no key":
                {"items": [{k: v for k, v in complete.items() if k != "key"}]},
            "an item with no content":
                {"items": [{k: v for k, v in complete.items() if k != "content"}]},
            "a warnings value that is null": {"items": [complete], "warnings": None},
            "a source_revisions value that is a list of pairs":
                {"items": [{**complete, "source_revisions": [["node-a", "17"]]}]},
        }
        for position, (name, payload) in enumerate(shapes.items()):
            with self.subTest(shape=name):
                path = self.run_dir / f"bad-bundle-{position}.json"
                self._write_bundle(payload, path)
                escalation = self.run_dir / f"bad-escalation-{position}.json"
                report = self.run_dir / f"bad-report-{position}.json"
                code, out, err, transport = self._run(
                    self._argv(bundle=str(path), escalation_out=str(escalation),
                               report_out=str(report))
                )
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertEqual(json.loads(err)["status"], "error")
                self.assertEqual(transport.calls, [])
                self.assertFalse(escalation.exists())
                self.assertFalse(report.exists())

    def test_a_script_with_no_page_table_is_refused_before_anything_is_paid(self):
        # Prose the page parser cannot read as a storyboard is not a draft with
        # nothing wrong with it, and an empty successful report would read
        # exactly like a pass.
        prose = self.run_dir / "prose.md"
        prose.write_text("# 学会分享\n\n还没有写成逐页脚本。\n", encoding="utf-8")
        escalation = self.run_dir / "never-written.json"
        code, out, err, transport = self._run(
            self._argv(script=str(prose), escalation_out=str(escalation))
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("page table", json.loads(err)["error"])
        self.assertEqual(transport.calls, [])
        self.assertFalse(escalation.exists())

    def test_a_wordless_script_is_reported_as_nothing_to_screen(self):
        # A file that *is* a storyboard whose pages carry no text is a different
        # answer: there is nothing to judge, and the report has to say so.
        wordless = self.run_dir / "wordless.md"
        wordless.write_text(
            "# 学会分享\n\n| # | 页码 | Text | 插图 |\n| --- | --- | --- | --- |\n"
            "| 1 | 1 |  | 男孩抱着红球 |\n",
            encoding="utf-8",
        )
        code, out, err, transport = self._run(self._argv(script=str(wordless)))
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["page_count"], 1)
        self.assertEqual(payload["item_count"], 0)
        self.assertEqual(payload["summary"]["total"], 0)
        self.assertEqual(transport.calls, [])
        self.assertTrue(any("没有可筛页面窗口" in note for note in payload["warnings"]))

    def test_an_empty_catalog_is_reported_as_a_gap(self):
        # A page outside the constraint vocabulary carries no red line: the
        # catalog is then empty, which is a vocabulary gap rather than "this
        # draft has no red lines".
        page = {
            "key": "海外绘本/小老鼠迈尔斯/references",
            "doc_token": "doxcnExample", "revision_id": 17,
            "title": "参考资料", "content": "# 参考资料\n\n- 《森林的故事》\n",
            "source_revisions": {"node-a": "17"}, "status": "published",
            "index_synced": True,
        }
        path = self.run_dir / "non-constraint-bundle.json"
        self._write_bundle({"items": [page], "warnings": [], "offline": False}, path)
        answers = answers_for(())
        code, out, err, _ = self._run(
            self._argv(bundle=str(path)),
            responses=[TransportResponse(200, success_body(answers), {})],
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["catalog_size"], 0)
        self.assertTrue(payload["catalog_gap"])
        self.assertTrue(payload["summary"]["catalog_gap"])
        # The page-quality half of the screen still ran: the gap is named, not
        # allowed to swallow the rest of the screen.
        self.assertTrue(payload["summary"]["total"] > 0)
        self.assertTrue(any("红线词表为空" in note for note in payload["warnings"]))

    def test_the_experimental_operation_never_reports_a_skipped_review(self):
        code, out, err, _ = self._clear_screen()
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["calibration_status"], "experimental")
        self.assertTrue(payload["summary"]["screened_clear"])
        self.assertEqual(payload["may_skip_llm_review_count"], 0)
        self.assertEqual(payload["may_skip_llm_review_items"], [])
        self.assertTrue(
            any("不缩减普通 LLM 复核范围" in note for note in payload["warnings"])
        )

    def test_a_calibrated_operation_counts_the_items_it_may_skip(self):
        """The reduction is per item, so the report counts items, not a verdict.

        A report-wide "the review may be skipped" flag would read as permission
        to drop the whole review, and it would travel inside the very package
        the plain LLM is asked to check.
        """

        policy = json.loads(Path(default_policy_path()).read_text(encoding="utf-8"))
        policy["operations"]["text_quality_prefilter"]["calibration_status"] = "calibrated"
        policy_path = self.run_dir / "calibrated-policy.json"
        policy_path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
        escalation_path = self.run_dir / "escalation.json"
        code, out, err, _ = self._clear_screen(
            policy=str(policy_path), escalation_out=str(escalation_path)
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["calibration_status"], "calibrated")
        self.assertEqual(
            payload["may_skip_llm_review_count"], payload["summary"]["screened_clear"]
        )
        self.assertTrue(payload["may_skip_llm_review_items"])
        package = json.loads(escalation_path.read_text(encoding="utf-8"))
        self.assertNotIn("may_skip_llm_review", package)
        self.assertEqual(package["may_skip_llm_review_count"],
                         payload["may_skip_llm_review_count"])

    def test_a_failed_call_never_reads_as_a_pass(self):
        code, out, err, transport = self._run(
            self._argv(), responses=[TransportResponse(500, "boom", {})]
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(payload["results"][0]["status"], "failed")
        self.assertEqual(payload["summary"]["screened_clear"], 0)
        self.assertEqual(payload["summary"]["runtime_failure"], payload["summary"]["total"])
        self.assertTrue(payload["escalation_package"])
        self.assertTrue(all(item["reason"] == "failed:http_500"
                            for item in payload["escalation_package"]))

    def test_a_literal_hit_that_was_not_escalated_is_reported_as_a_conflict(self):
        self.script_path.write_text(LITERAL_SCRIPT, encoding="utf-8")
        rules = catalog_from_bundle(bundle())
        code, out, err, _ = self._run(
            self._argv(),
            responses=[TransportResponse(200, success_body(answers_for(rules)), {})],
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["proxy_hit_count"], 1)
        self.assertTrue(payload["summary"]["proxy_conflicts"])
        self.assertEqual(payload["proxy_conflicts"][0]["reason"],
                         "proxy_hit_not_escalated")

    def test_an_escalated_red_line_is_not_reported_as_a_conflict(self):
        self.script_path.write_text(LITERAL_SCRIPT, encoding="utf-8")
        rules = catalog_from_bundle(bundle())
        answers = answers_for(rules, overrides={redline_question_id(rules): 0.93})
        code, out, err, _ = self._run(
            self._argv(),
            responses=[TransportResponse(200, success_body(answers), {})],
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["proxy_hit_count"], 1)
        self.assertEqual(payload["proxy_conflicts"], [])
        escalated = [route for route in payload["routes"]
                     if route["item_id"] == redline_question_id(rules)]
        self.assertEqual(len(escalated), 1)


if __name__ == "__main__":
    unittest.main()
