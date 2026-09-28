import io
import json
import tempfile
import unittest
from pathlib import Path

from calibrate import (
    LABELS,
    CalibrationSample,
    SampleOutcome,
    load_outcomes,
    load_samples,
    main,
    parse_samples,
    recommend,
    sweep,
)

SAMPLES_MD = """# 校准样本

| sample_id | operation | item_id | dimension | label | evidence | text |
| --- | --- | --- | --- | --- | --- | --- |
| s1 | text_quality_prefilter | page-1 | direct_moralizing | issue | 直接说教 | 所以我们要学会分享。 |
| s2 | text_quality_prefilter | page-2 | direct_moralizing | clear | 用动作呈现 | 迈尔斯把球推了过去。 |
| s3 | text_quality_prefilter | page-3 | read_aloud_friction | borderline | 略拗口 | 他把球推过去给她。 |
| s4 | text_quality_prefilter | page-4 | direct_moralizing | issue | 双重否定 | 他不是不想分享。 |
"""

SAMPLES_PATH = (
    Path(__file__).resolve().parents[1] / "references" / "calibration-samples.md"
)


def sample(sample_id, label="issue", operation="text_quality_prefilter"):
    return CalibrationSample(sample_id, operation, f"page-{sample_id}",
                             "direct_moralizing", label, "", "")


def outcome(sample_id, probability, label="issue", failed=False):
    return SampleOutcome(sample(sample_id, label), probability, failed)


class LabelTests(unittest.TestCase):
    def test_the_label_vocabulary_is_fixed(self):
        self.assertEqual(LABELS, ("clear", "issue", "borderline"))


class ParseSamplesTests(unittest.TestCase):
    def test_samples_are_read_from_the_table(self):
        samples = parse_samples(SAMPLES_MD)
        self.assertEqual([entry.sample_id for entry in samples],
                         ["s1", "s2", "s3", "s4"])

    def test_the_legend_table_and_prose_are_not_read_as_samples(self):
        legend = (
            "## 标注口径\n\n"
            "| label | 含义 |\n| --- | --- |\n| `clear` | 可以安全标记 |\n"
        )
        self.assertEqual(parse_samples(legend), ())
        self.assertEqual(parse_samples(SAMPLES_MD.split("| --- ")[0]), ())

    def test_the_annotation_is_read_column_by_column(self):
        first = parse_samples(SAMPLES_MD)[0]
        self.assertEqual(first.dimension, "direct_moralizing")
        self.assertEqual(first.label, "issue")
        self.assertEqual(first.evidence, "直接说教")
        self.assertEqual(first.text, "所以我们要学会分享。")

    def test_labels_are_validated(self):
        with self.assertRaises(ValueError):
            parse_samples(SAMPLES_MD.replace("| clear |", "| maybe |"))

    def test_an_unknown_operation_is_refused(self):
        with self.assertRaises(ValueError):
            parse_samples(SAMPLES_MD.replace("text_quality_prefilter", "write_the_story"))

    def test_the_shipped_sample_file_parses(self):
        samples = load_samples(SAMPLES_PATH)
        self.assertTrue(samples)
        labels = {entry.label for entry in samples}
        self.assertTrue(labels <= set(LABELS))

    def test_the_shipped_sample_file_covers_the_spec_scenarios(self):
        samples = load_samples(SAMPLES_PATH)
        dimensions = {entry.dimension for entry in samples}
        self.assertIn("direct_moralizing", dimensions)
        self.assertIn("emotion_told_not_shown", dimensions)
        self.assertIn("read_aloud_friction", dimensions)
        self.assertEqual(
            {entry.operation for entry in samples},
            {"text_quality_prefilter", "knowledge_relevance"},
        )
        self.assertIn("borderline", {entry.label for entry in samples})
        # 否定 is the annotator's vocabulary, so the negation case is annotated
        # in `evidence`; a story line rarely contains the meta term itself.
        annotations = " ".join(
            f"{entry.text} {entry.evidence}" for entry in samples
        )
        self.assertIn("否定", annotations)
        self.assertIn("末页", annotations)


class LoadSamplesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_sample_file_with_no_readable_row_is_refused(self):
        path = self.dir / "empty.md"
        path.write_text("# 没有样本\n\n这里没有表格。\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_samples(path)


class LoadOutcomesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, text):
        path = self.dir / "outcomes.json"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_measurement_file_must_be_a_json_array(self):
        with self.assertRaises(ValueError):
            load_outcomes(self.write('{"sample_id": "s1"}'))

    def test_an_unreadable_measurement_must_be_marked_failed(self):
        with self.assertRaises(ValueError):
            load_outcomes(self.write('[{"sample_id": "s1"}]'))
        with self.assertRaises(ValueError):
            load_outcomes(self.write('[{"sample_id": "s1", "probability": null}]'))
        with self.assertRaises(ValueError):
            load_outcomes(self.write(
                '[{"sample_id": "s1", "probability": "unknown"}]'
            ))
        with self.assertRaises(ValueError):
            load_outcomes(self.write('[{"sample_id": "s1", "probability": 1.4}]'))
        with self.assertRaises(ValueError):
            load_outcomes(self.write(
                '[{"sample_id": "s1", "probability": 0.4, "failed": "no"}]'
            ))
        outcomes = load_outcomes(self.write('[{"sample_id": "s1", "failed": true}]'))
        self.assertTrue(outcomes[0].failed)
        self.assertIsNone(outcomes[0].probability)

    def test_a_measurement_never_carries_its_own_label(self):
        outcomes = load_outcomes(self.write(
            '[{"sample_id": "s1", "probability": 0.4, "label": "clear"}]'
        ))
        self.assertEqual(outcomes[0].sample.label, "")


class SweepTests(unittest.TestCase):
    def outcomes(self):
        return (
            outcome("a", 0.05, "clear"), outcome("b", 0.10, "clear"),
            outcome("c", 0.50, "borderline"),
            outcome("d", 0.85), outcome("e", 0.95),   # issue ...
        )

    def test_the_sweep_has_one_confusion_matrix_per_threshold(self):
        rows = sweep(self.outcomes(), ("0.25", "0.50", "0.75"))
        self.assertEqual([row["threshold"] for row in rows],
                         ["0.25", "0.50", "0.75"])

    def test_a_higher_threshold_clears_more_and_escalates_less(self):
        lower, higher = sweep(self.outcomes(), ("0.25", "0.75"))
        self.assertLess(lower["screened_clear"], higher["screened_clear"])
        self.assertLess(higher["escalated"], lower["escalated"])

    def test_the_clear_boundary_is_inclusive_and_compared_exactly(self):
        row = sweep(
            (outcome("at", 0.25), outcome("over", 0.2500001)), ("0.25",)
        )[0]
        self.assertEqual(row["screened_clear"], 1)
        self.assertEqual(row["threshold"], "0.25")

    def test_failed_outcomes_are_counted_separately_and_never_as_clear(self):
        outcomes = (outcome("f", 0.05, failed=True),)
        row = sweep(outcomes, ("0.25",))[0]
        self.assertEqual(row["failed"], 1)
        self.assertEqual(row["screened_clear"], 0)
        self.assertEqual(row["escalation_rate"], 1.0)

    def test_an_unreadable_measurement_is_never_clear(self):
        broken = (
            SampleOutcome(sample("x", "clear"), None, False),
            SampleOutcome(sample("y", "clear"), 1.4, False),
            SampleOutcome(sample("z", "clear"), float("nan"), False),
        )
        row = sweep(broken, ("0.90",))[0]
        self.assertEqual(row["screened_clear"], 0)
        self.assertEqual(row["failed"], 3)

    def test_borderline_clears_are_reported_so_a_disagreement_is_visible(self):
        row = sweep(self.outcomes(), ("0.60",))[0]
        self.assertEqual(row["borderline_cleared"], 1)


class RecommendTests(unittest.TestCase):
    def outcomes(self):
        return (
            outcome("a", 0.05, "clear"), outcome("b", 0.10, "clear"),
            outcome("c", 0.50, "borderline"),
            outcome("d", 0.85), outcome("e", 0.95),   # issue ...
        )

    def test_a_recommended_threshold_has_no_false_negative(self):
        result = recommend(self.outcomes(), max_false_negative_rate=0.0)
        self.assertEqual(result["false_negatives"], 0)
        self.assertEqual(float(result["threshold"]), 0.80)

    def test_the_recommendation_names_a_threshold_and_its_rates(self):
        result = recommend(self.outcomes(), max_false_negative_rate=0.0)
        self.assertIn("threshold", result)
        self.assertIn("escalation_rate", result)
        self.assertIn("false_negative_rate", result)

    def test_a_stricter_rate_can_only_lower_the_threshold(self):
        grid = ("0.50", "0.90")
        loose = recommend(
            self.outcomes(), max_false_negative_rate=0.5, thresholds=grid
        )
        strict = recommend(
            self.outcomes(), max_false_negative_rate=0.0, thresholds=grid
        )
        self.assertLess(float(strict["threshold"]), float(loose["threshold"]))
        self.assertLess(strict["screened_clear"], loose["screened_clear"])

    def test_it_refuses_to_recommend_from_too_few_samples(self):
        with self.assertRaises(ValueError):
            recommend((outcome("a", 0.05),), max_false_negative_rate=0.0)

    def test_it_refuses_a_threshold_when_every_candidate_loses_a_red_line(self):
        red_lines = tuple(outcome(f"r{index}", 0.05) for index in range(4))
        result = recommend(red_lines, max_false_negative_rate=0.0)
        self.assertIsNone(result["threshold"])
        self.assertIn("reason", result)
        self.assertEqual(result["samples"], 4)


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_samples(self, text=SAMPLES_MD):
        path = self.dir / "samples.md"
        path.write_text(text, encoding="utf-8")
        return path

    def write_outcomes(self, entries):
        path = self.dir / "outcomes.json"
        path.write_text(json.dumps(entries), encoding="utf-8")
        return path

    def test_the_report_is_printed_as_json(self):
        samples = self.write_samples()
        outcomes = self.write_outcomes([
            {"sample_id": "s1", "probability": 0.9, "failed": False},
            {"sample_id": "s2", "probability": 0.1, "failed": False},
            {"sample_id": "s3", "probability": 0.5, "failed": False},
            {"sample_id": "s4", "probability": 0.8, "failed": False},
        ])
        stdout, stderr = io.StringIO(), io.StringIO()
        code = main(["--samples", str(samples), "--outcomes", str(outcomes),
                     "--operation", "text_quality_prefilter",
                     "--max-false-negative-rate", "0.5"],
                    stdout=stdout, stderr=stderr)
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["operation"], "text_quality_prefilter")
        self.assertIn("sweep", payload)
        self.assertIn("recommendation", payload)
        self.assertEqual(payload["samples"], 4)
        self.assertEqual(payload["ignored_outcomes"], 0)
        self.assertIn("threshold", payload["recommendation"])

    def test_only_the_named_operation_is_measured(self):
        samples = self.write_samples(SAMPLES_MD + (
            "\n| k1 | knowledge_relevance | chunk-1 | relevant | clear | 无关 "
            "| 海外市场定价区间。 |\n"
        ))
        outcomes = self.write_outcomes([
            {"sample_id": "s1", "probability": 0.9, "failed": False},
            {"sample_id": "s2", "probability": 0.1, "failed": False},
            {"sample_id": "s3", "probability": 0.5, "failed": False},
            {"sample_id": "s4", "probability": 0.8, "failed": False},
            {"sample_id": "k1", "probability": 0.1, "failed": False},
        ])
        stdout = io.StringIO()
        code = main(["--samples", str(samples), "--outcomes", str(outcomes),
                     "--operation", "text_quality_prefilter"],
                    stdout=stdout, stderr=io.StringIO())
        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["samples"], 4)
        self.assertEqual(payload["ignored_outcomes"], 1)

    def test_no_measurement_means_no_recommendation(self):
        samples = self.write_samples()
        outcomes = self.write_outcomes([])
        stdout, stderr = io.StringIO(), io.StringIO()
        code = main(["--samples", str(samples), "--outcomes", str(outcomes),
                     "--operation", "text_quality_prefilter"],
                    stdout=stdout, stderr=stderr)
        self.assertEqual(code, 1)
        self.assertIn("error", stderr.getvalue().lower())

    def test_a_measurement_for_an_unknown_sample_is_refused(self):
        samples = self.write_samples()
        outcomes = self.write_outcomes([
            {"sample_id": "typo", "probability": 0.1, "failed": False},
        ])
        stderr = io.StringIO()
        code = main(["--samples", str(samples), "--outcomes", str(outcomes),
                     "--operation", "text_quality_prefilter"],
                    stdout=io.StringIO(), stderr=stderr)
        self.assertEqual(code, 1)
        self.assertIn("typo", stderr.getvalue())

    def test_no_arguments_prints_usage(self):
        stderr = io.StringIO()
        self.assertEqual(main([], stdout=io.StringIO(), stderr=stderr), 2)
        self.assertIn("calibrate.py", stderr.getvalue())

    def test_the_shipped_sample_file_is_calibratable(self):
        # The shipped set has to be able to express a working boundary: an
        # `issue` sample must outrank every candidate boundary while a `clear`
        # one stays under it. This asserts the sample set is usable, not that
        # any particular threshold is the right one.
        samples = load_samples(SAMPLES_PATH)
        rubric = {"clear": 0.05, "borderline": 0.50, "issue": 0.90}
        outcomes = self.write_outcomes([
            {"sample_id": entry.sample_id, "probability": rubric[entry.label],
             "failed": False}
            for entry in samples
        ])
        stdout = io.StringIO()
        code = main(["--samples", str(SAMPLES_PATH), "--outcomes", str(outcomes),
                     "--operation", "text_quality_prefilter"],
                    stdout=stdout, stderr=io.StringIO())
        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        page_samples = [
            entry for entry in samples
            if entry.operation == "text_quality_prefilter"
        ]
        self.assertEqual(payload["samples"], len(page_samples))
        self.assertEqual(
            payload["ignored_outcomes"], len(samples) - len(page_samples)
        )
        self.assertEqual(payload["recommendation"]["false_negatives"], 0)
        self.assertIsNotNone(payload["recommendation"]["threshold"])
        self.assertEqual(len(payload["sweep"]), 11)


if __name__ == "__main__":
    unittest.main()
