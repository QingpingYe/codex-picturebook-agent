import unittest

from quality_gate import (
    Finding,
    SemanticJudgment,
    apply_judgments,
    build_report,
    report_to_json,
    report_to_markdown,
)


class QualityGateTests(unittest.TestCase):
    def test_project_fail_blocks(self):
        finding = Finding("R1", "project", "FAIL", "违反角色红线", "角色不能飞行")
        report = build_report([finding])
        self.assertEqual(report.status, "blocked")

    def test_craft_fail_only_warns(self):
        finding = Finding("C1", "craft", "FAIL", "翻页钩子密度不足", "平均 6 页")
        report = build_report([finding])
        self.assertEqual(report.status, "needs_user_decision")

    def test_pass_judgment_downgrades_flag(self):
        finding = Finding("R1", "project", "FAIL", "疑似红线", "角色不能飞行")
        judgment = SemanticJudgment("R1", "PASS", "上下文是玩具飞机", "上下文")
        result = apply_judgments([finding], [judgment])
        self.assertEqual(result[0].severity, "PASS")

    def test_missing_judgment_keeps_fail(self):
        finding = Finding("R1", "project", "FAIL", "疑似红线", "角色不能飞行")
        result = apply_judgments([finding], [])
        self.assertEqual(result[0].severity, "FAIL")

    def test_report_has_machine_readable_json(self):
        finding = Finding("R1", "project", "FAIL", "违反角色红线", "角色不能飞行")
        report = build_report([finding])
        self.assertEqual(report_to_json(report), {
            "status": "blocked",
            "findings": [{
                "id": "R1",
                "source": "project",
                "severity": "FAIL",
                "message": "违反角色红线",
                "evidence": "角色不能飞行",
            }],
            "blocked_reasons": ["违反角色红线"],
        })


if __name__ == "__main__":
    unittest.main()
