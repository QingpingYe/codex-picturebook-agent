import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from merge_protocol import MergeDecision, MergeValidationError, classify, validate_decision


def valid_page(body):
    return (
        body.rstrip("\n")
        + "\n\n## 系统元数据（请勿编辑）\n```json\n"
        + '{"key":"s/p/worldview","page_type":"worldview","source_node_tokens":["source"],'
        + '"source_revisions":{"source":"r1"},"last_ai_revision_id":1,"schema_version":1}'
        + "\n```\n"
    )


def decision(action="publish", merged_markdown=None, reason="人工内容已保留"):
    return MergeDecision(key="s/p/worldview", action=action,
                         merged_markdown=merged_markdown, reason=reason)


class MergeProtocolTests(unittest.TestCase):
    def test_source_only_change_can_publish(self):
        result = classify(base="# A", current="# A", candidate="# B", source_changed=True)
        self.assertEqual(result.required_action, "publish")

    def test_human_and_source_change_requires_agent_decision(self):
        result = classify(base="# A", current="# 人工规则", candidate="# 新源规则", source_changed=True)
        self.assertEqual(result.required_action, "agent_decision")

    def test_publish_that_drops_human_line_is_rejected(self):
        requirement = classify(base="# A", current="# A\n\n人工规则", candidate="# B", source_changed=True)
        with self.assertRaisesRegex(MergeValidationError, "人工"):
            validate_decision(requirement, "# A", "# A\n\n人工规则",
                              decision(merged_markdown=valid_page("# B")))

    def test_publish_that_reintroduces_human_deleted_content_is_rejected(self):
        requirement = classify(base="# A\n\n旧设定", current="# A", candidate="# A\n\n新资料", source_changed=True)
        with self.assertRaisesRegex(MergeValidationError, "人工删除"):
            validate_decision(requirement, "# A\n\n旧设定", "# A",
                              decision(merged_markdown=valid_page("# A\n\n新资料\n\n旧设定")))

    def test_publish_accepts_merged_human_and_source_lines(self):
        requirement = classify(base="# A", current="# A\n\n人工规则", candidate="# A\n\n新资料", source_changed=True)
        result = validate_decision(requirement, "# A", "# A\n\n人工规则",
                                   decision(merged_markdown=valid_page("# A\n\n人工规则\n\n新资料")))
        self.assertEqual(result.action, "publish")


if __name__ == "__main__":
    unittest.main()
