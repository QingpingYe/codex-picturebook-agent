import unittest

from quality_gate import (
    AUTHORITY_SOURCE,
    PROXY_SOURCE,
    Finding,
    SemanticJudgment,
    apply_judgments,
    build_report,
    promote_confirmed_redlines,
    report_to_json,
    report_to_markdown,
    resolve_findings,
)


def proxy(finding_id="R1", severity="FAIL"):
    return Finding(finding_id, PROXY_SOURCE, severity, "疑似红线", "角色不能飞行")


def authority(finding_id="R1", severity="FAIL"):
    return Finding(finding_id, AUTHORITY_SOURCE, severity, "确认违反角色红线", "角色不能飞行")


class BuildReportTests(unittest.TestCase):
    def test_an_authoritative_violation_blocks(self):
        self.assertEqual(build_report([authority()]).status, "blocked")

    def test_a_proxy_hit_alone_does_not_block(self):
        report = build_report([proxy()])
        self.assertEqual(report.status, "needs_user_decision")
        self.assertEqual(report.blocked_reasons, ())

    def test_a_craft_failure_only_needs_a_decision(self):
        finding = Finding("C1", "craft", "FAIL", "翻页钩子密度不足", "平均 6 页")
        self.assertEqual(build_report([finding]).status, "needs_user_decision")

    def test_a_wiki_failure_only_needs_a_decision(self):
        finding = Finding("W1", "wiki", "FAIL", "索引缺失", "index.md")
        self.assertEqual(build_report([finding]).status, "needs_user_decision")

    def test_no_failure_passes(self):
        self.assertEqual(build_report([proxy(severity="WARN")]).status, "passed")

    def test_blocked_reasons_name_every_authoritative_violation(self):
        report = build_report([authority("R1"), authority("R2")])
        self.assertEqual(len(report.blocked_reasons), 2)

    def test_a_downgraded_authority_still_blocks(self):
        # severity PASS on an authority source is still a recorded violation.
        self.assertEqual(build_report([authority(severity="PASS")]).status,
                         "needs_user_decision")


class ApplyJudgmentsTests(unittest.TestCase):
    def test_a_pass_judgment_clears_a_proxy_hit(self):
        result = apply_judgments([proxy()], [SemanticJudgment("R1", "PASS", "上下文是玩具飞机", "")])
        self.assertEqual(result[0].severity, "PASS")
        self.assertEqual(result[0].source, PROXY_SOURCE)

    def test_a_pass_judgment_cannot_clear_an_authoritative_violation(self):
        result = apply_judgments([authority()], [SemanticJudgment("R1", "PASS", "看起来没事", "")])
        self.assertEqual(result[0].severity, "FAIL")
        self.assertEqual(result[0].source, AUTHORITY_SOURCE)

    def test_an_authoritative_violation_keeps_its_rationale_as_context(self):
        result = apply_judgments([authority()], [SemanticJudgment("R1", "PASS", "已复核", "")])
        self.assertIn("已复核", result[0].message)
        self.assertEqual(build_report(result).status, "blocked")

    def test_an_unmatched_finding_passes_through_unchanged(self):
        result = apply_judgments([proxy("R9")], [SemanticJudgment("R1", "PASS", "x", "")])
        self.assertEqual(result[0].severity, "FAIL")

    def test_an_invalid_verdict_is_refused(self):
        with self.assertRaises(ValueError):
            apply_judgments([proxy()], [SemanticJudgment("R1", "MAYBE", "x", "")])


class PromoteConfirmedRedlinesTests(unittest.TestCase):
    def test_a_confirmed_proxy_hit_becomes_authoritative(self):
        result = promote_confirmed_redlines(
            [proxy()], [SemanticJudgment("R1", "FAIL", "确实违反", "第 5 页原文")]
        )
        self.assertEqual(result[0].source, AUTHORITY_SOURCE)
        self.assertEqual(result[0].severity, "FAIL")
        self.assertIn("终审确认", result[0].message)

    def test_an_unconfirmed_proxy_hit_stays_a_candidate(self):
        result = promote_confirmed_redlines(
            [proxy()], [SemanticJudgment("R1", "WARN", "不确定", "")]
        )
        self.assertEqual(result[0].source, PROXY_SOURCE)

    def test_an_authoritative_finding_is_never_demoted(self):
        result = promote_confirmed_redlines(
            [authority()], [SemanticJudgment("R1", "PASS", "x", "")]
        )
        self.assertEqual(result[0].source, AUTHORITY_SOURCE)


class ResolveFindingsTests(unittest.TestCase):
    def test_a_confirmed_red_line_blocks_after_resolution(self):
        resolved = resolve_findings(
            [proxy()], [SemanticJudgment("R1", "FAIL", "确实违反", "第 5 页")]
        )
        self.assertEqual(build_report(resolved).status, "blocked")

    def test_a_cleared_red_line_does_not_block_after_resolution(self):
        resolved = resolve_findings(
            [proxy()], [SemanticJudgment("R1", "PASS", "玩具飞机", "上下文")]
        )
        self.assertEqual(build_report(resolved).status, "passed")


class RenderTests(unittest.TestCase):
    def test_json_and_markdown_keep_their_shapes(self):
        report = build_report([proxy(), authority("R2")])
        payload = report_to_json(report)
        self.assertIsInstance(payload["findings"], list)
        self.assertIsInstance(payload["blocked_reasons"], list)
        text = report_to_markdown(report)
        self.assertIn("## 质量报告", text)
        self.assertIn("阻断原因：", text)


if __name__ == "__main__":
    unittest.main()
