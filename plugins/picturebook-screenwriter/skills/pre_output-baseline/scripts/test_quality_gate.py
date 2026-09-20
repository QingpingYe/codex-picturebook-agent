import unittest

from quality_gate import Finding, build_report, report_to_markdown


class QualityGateTests(unittest.TestCase):
    def test_project_fail_blocks(self):
        finding = Finding("R1", "project", "FAIL", "违反角色红线", "角色不能飞行")
        report = build_report([finding])
        self.assertEqual(report.status, "blocked")

    def test_craft_fail_only_warns(self):
        finding = Finding("C1", "craft", "FAIL", "翻页钩子密度不足", "平均 6 页")
        report = build_report([finding])
        self.assertEqual(report.status, "needs_user_decision")


if __name__ == "__main__":
    unittest.main()
