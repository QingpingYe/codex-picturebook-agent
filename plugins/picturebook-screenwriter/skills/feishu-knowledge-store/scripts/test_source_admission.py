#!/usr/bin/env python3
"""AI_KB_SOURCE_ADMISSION_V1 contract and subtree resolver tests."""

import json
from pathlib import Path
import unittest

import source_admission as sa


FIXTURE = Path(__file__).with_name("fixtures") / "source_admission_v1.json"


def payload(entries=None, version=1):
    return {"schema_version": version, "entries": entries or []}


def entry(token, decision="admit", **updates):
    value = {
        "token": token,
        "title": f"{token} 标题",
        "decision": decision,
        "decided_by": "user",
        "decided_at": "2026-09-23T10:00:00+08:00",
        "reason": "用户裁定",
    }
    value.update(updates)
    return value


def snapshot(nodes):
    return {"nodes": nodes}


class TestSourceAdmission(unittest.TestCase):

    def test_missing_page_is_empty_policy(self):
        policy = sa.empty_admission_policy(page_present=False)
        result = sa.resolve_source_admission(policy, snapshot({"a": {}}))
        self.assertEqual(result.excluded_tokens, frozenset())
        self.assertFalse(result.page_present)

    def test_fixture_has_exact_six_fields(self):
        policy = sa.parse_admission_payload(json.loads(FIXTURE.read_text(encoding="utf-8")))
        self.assertEqual(len(policy.entries), 2)
        self.assertEqual(
            set(policy.entries[0].as_dict()),
            {"token", "title", "decision", "decided_by", "decided_at", "reason"},
        )

    def test_parse_control_page_heading_and_json(self):
        page = "# AI_KB_SOURCE_ADMISSION_V1\n```json\n" + json.dumps(payload([entry("tokA")])) + "\n```\n"
        policy = sa.parse_source_admission(page)
        self.assertEqual(policy.entries[0].token, "tokA")

    def test_control_page_allows_feishu_blank_line_round_trip(self):
        page = "# AI_KB_SOURCE_ADMISSION_V1\n\n```json\n" + json.dumps(payload([entry("tokA")])) + "\n```\n"
        policy = sa.parse_source_admission(page)
        self.assertEqual(policy.entries[0].token, "tokA")

    def test_unknown_field_and_wrong_schema_version_fail(self):
        bad = entry("tokA")
        bad["extra"] = "no"
        with self.assertRaises(sa.SourceAdmissionError):
            sa.parse_admission_payload(payload([bad]))
        with self.assertRaises(sa.SourceAdmissionError):
            sa.parse_admission_payload(payload([entry("tokA")], version=2))

    def test_six_fields_reject_empty_values(self):
        for field in ("token", "title", "decision", "decided_by", "decided_at", "reason"):
            with self.subTest(field=field):
                bad = entry("tokA")
                bad[field] = ""
                with self.assertRaises(sa.SourceAdmissionError):
                    sa.parse_admission_payload(payload([bad]))

    def test_duplicate_token_fails(self):
        with self.assertRaises(sa.SourceAdmissionError):
            sa.parse_admission_payload(payload([entry("tokA"), entry("tokA", "exclude")]))

    def test_admit_has_no_admission_effect(self):
        policy = sa.parse_admission_payload(payload([entry("tokA", "admit")]))
        result = sa.resolve_source_admission(policy, snapshot({"tokA": {}}))
        self.assertNotIn("tokA", result.excluded_tokens)
        self.assertIn("tokA", result.included_tokens)

    def test_exclude_marks_node_container_and_all_descendants(self):
        policy = sa.parse_admission_payload(payload([entry("container", "exclude")]))
        nodes = {
            "root": {"parent_node_token": ""},
            "container": {"parent_node_token": "root", "has_child": True},
            "child": {"parent_node_token": "container"},
            "grandchild": {"parent_node_token": "child"},
            "other": {"parent_node_token": "root"},
        }
        result = sa.resolve_source_admission(policy, snapshot(nodes))
        self.assertEqual(result.excluded_tokens,
                         frozenset({"container", "child", "grandchild"}))
        self.assertEqual(result.excluded_containers, frozenset({"container"}))
        self.assertIn("other", result.included_tokens)

    def test_empty_policy_does_not_require_complete_ancestry(self):
        policy = sa.empty_admission_policy(page_present=False)
        nodes = {"child": {"parent_node_token": "missing"}}
        result = sa.resolve_source_admission(policy, snapshot(nodes))
        self.assertIn("child", result.included_tokens)

    def test_noncritical_missing_parent_or_cycle_stops(self):
        policy = sa.parse_admission_payload(payload([entry("excluded", "exclude")]))
        with self.assertRaises(sa.DanglingAncestryError) as missing:
            sa.resolve_source_admission(policy, snapshot({
                "excluded": {"parent_node_token": "missing"},
                "other": {"parent_node_token": "also-missing"},
            }))
        self.assertIn("excluded", missing.exception.tokens)

        with self.assertRaises(sa.DanglingAncestryError) as cycle:
            sa.resolve_source_admission(policy, snapshot({
                "excluded": {"parent_node_token": "a"},
                "a": {"parent_node_token": "excluded"},
            }))
        self.assertIn("excluded", cycle.exception.tokens)


if __name__ == "__main__":
    unittest.main()