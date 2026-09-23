import unittest
from dataclasses import replace

from chunker import Chunk
from recall import ARTIFACT_PAGE_TYPES, partition, recall_candidates


def chunk(key, heading, required=False, text=None):
    return Chunk(
        chunk_id=f"{key}#{heading or '000'}",
        key=key,
        doc_token="doxcnExample",
        revision_id=17,
        heading_path=(heading,) if heading else (),
        text=text if text is not None else f"## {heading}\n\n正文。",
        required=required,
    )


SCRIPT_KEY = "海外绘本/小老鼠迈尔斯/worldview"
MARKET_KEY = "common/common/market-research"


class RecallTests(unittest.TestCase):
    def test_a_required_chunk_is_never_a_candidate(self):
        candidates = recall_candidates(
            [chunk(SCRIPT_KEY, "创作红线不变量", required=True)],
            artifact_type="script",
        )
        self.assertEqual(candidates, ())

    def test_a_soft_chunk_from_an_in_scope_page_is_a_candidate(self):
        candidates = recall_candidates(
            [chunk(SCRIPT_KEY, "场景清单")], artifact_type="script"
        )
        self.assertEqual(len(candidates), 1)

    def test_a_soft_chunk_from_an_out_of_scope_page_is_not_a_candidate(self):
        candidates = recall_candidates(
            [chunk(MARKET_KEY, "竞品概览")], artifact_type="script"
        )
        self.assertEqual(candidates, ())

    def test_a_term_hit_recalls_a_chunk_from_an_out_of_scope_page(self):
        candidates = recall_candidates(
            [chunk(MARKET_KEY, "竞品概览", text="## 竞品概览\n\n迈尔斯系列的销量。")],
            artifact_type="script",
            terms=("迈尔斯",),
        )
        self.assertEqual(len(candidates), 1)

    def test_scoping_differs_between_artifact_types(self):
        chunks = [chunk(MARKET_KEY, "竞品概览")]
        self.assertEqual(recall_candidates(chunks, artifact_type="script"), ())
        self.assertEqual(len(recall_candidates(chunks, artifact_type="positioning")), 1)

    def test_an_unknown_artifact_type_recalls_nothing(self):
        candidates = recall_candidates(
            [chunk(SCRIPT_KEY, "场景清单")], artifact_type="not_an_artifact"
        )
        self.assertEqual(candidates, ())

    def test_every_declared_artifact_type_has_a_page_scope(self):
        from dependencies import ARTIFACT_TYPES

        self.assertEqual(set(ARTIFACT_PAGE_TYPES), set(ARTIFACT_TYPES))


class PartitionTests(unittest.TestCase):
    def test_partition_splits_required_and_recalled_from_the_rest(self):
        required = chunk(SCRIPT_KEY, "创作红线不变量", required=True)
        candidate = chunk(SCRIPT_KEY, "场景清单")
        unrecalled = chunk(MARKET_KEY, "竞品概览")
        always, candidates = partition(
            [required, candidate, unrecalled], artifact_type="script"
        )
        self.assertEqual(
            sorted(item.chunk_id for item in always),
            sorted([required.chunk_id, unrecalled.chunk_id]),
        )
        self.assertEqual([item.chunk_id for item in candidates], [candidate.chunk_id])

    def test_partition_keeps_list_order(self):
        first = chunk(SCRIPT_KEY, "甲")
        second = chunk(SCRIPT_KEY, "乙")
        _, candidates = partition([first, second], artifact_type="script")
        self.assertEqual([item.chunk_id for item in candidates],
                         [first.chunk_id, second.chunk_id])

    def test_a_chunk_id_collision_never_hides_a_required_chunk(self):
        # Ids are page key plus section ordinal, so two chunks can carry the
        # same id. A required chunk that shares its id with a candidate must
        # still reach `always_kept` instead of vanishing from both lists.
        soft = chunk(SCRIPT_KEY, "场景清单")
        required_twin = replace(soft, required=True, required_reason="caller_declared")
        always, candidates = partition(
            [soft, required_twin], artifact_type="script"
        )
        self.assertEqual([item.chunk_id for item in always], [required_twin.chunk_id])
        self.assertEqual([item.chunk_id for item in candidates], [soft.chunk_id])


if __name__ == "__main__":
    unittest.main()
