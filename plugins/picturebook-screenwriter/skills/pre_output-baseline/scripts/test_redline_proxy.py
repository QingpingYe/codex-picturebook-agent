import unittest

from redline_proxy import scan_redlines


class RedlineProxyTests(unittest.TestCase):
    def test_forbidden_term_is_found(self):
        rules = (("R1", "飞行", "角色不能飞行"),)
        findings = scan_redlines("迈尔斯开始飞行。", rules)
        self.assertEqual(findings[0].id, "R1")
        self.assertEqual(findings[0].severity, "FAIL")

    def test_empty_rules_return_no_findings(self):
        self.assertEqual(scan_redlines("任意文本", ()), ())


if __name__ == "__main__":
    unittest.main()
