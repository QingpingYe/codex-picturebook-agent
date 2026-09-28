import unittest

from quality_gate import PROXY_SOURCE, build_report
from redline_proxy import scan_redlines


class RedlineProxyTests(unittest.TestCase):
    def test_a_literal_hit_is_produced_as_a_candidate_not_a_verdict(self):
        rules = (("R1", "飞行", "角色不能飞行"),)
        findings = scan_redlines("迈尔斯学会了飞行。", rules)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].source, PROXY_SOURCE)
        self.assertEqual(findings[0].id, "R1")
        self.assertEqual(findings[0].evidence, "角色不能飞行")
        self.assertIn("飞行", findings[0].message)

    def test_a_candidate_hit_never_blocks_on_its_own(self):
        rules = (("R1", "飞行", "角色不能飞行"),)
        report = build_report(scan_redlines("迈尔斯学会了飞行。", rules))
        self.assertEqual(report.status, "needs_user_decision")

    def test_a_negated_literal_hit_stays_a_proxy_candidate(self):
        # "迈尔斯没有飞行" contains the banned literal exactly like a real
        # violation does. That is the whole reason the literal scan cannot
        # decide anything by itself: the hit stays a candidate, keeps its
        # evidence, and still has to be screened before it can block.
        rules = (("R1", "飞行", "角色不能飞行"),)
        findings = scan_redlines("迈尔斯没有飞行。", rules)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].source, PROXY_SOURCE)
        report = build_report(findings)
        self.assertEqual(report.status, "needs_user_decision")
        self.assertEqual(report.blocked_reasons, ())

    def test_a_miss_produces_nothing(self):
        rules = (("R1", "飞行", "角色不能飞行"),)
        self.assertEqual(scan_redlines("迈尔斯走到了树边。", rules), ())

    def test_an_empty_pattern_is_skipped(self):
        rules = (("R1", "", "空规则"),)
        self.assertEqual(scan_redlines("任何文本", rules), ())


if __name__ == "__main__":
    unittest.main()
