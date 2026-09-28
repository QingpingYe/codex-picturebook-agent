"""Recall the soft chunks that Jev is allowed to consider for exclusion."""

from __future__ import annotations

from chunker import Chunk
from required_marking import page_type_of

# Which knowledge page types can plausibly inform each artifact type. This is a
# cost-control recall, not a relevance verdict: a page outside the scope is kept
# unconditionally rather than dropped.
ARTIFACT_PAGE_TYPES: dict[str, tuple[str, ...]] = {
    "positioning": (
        "ip-overview", "market-research", "creation-standards", "quality-rubric",
    ),
    "topic_plan": (
        "ip-overview", "market-research", "creation-standards",
        "story-fingerprint-spec", "creative-feature-ledger",
    ),
    "worldview": ("worldview", "creation-standards", "references"),
    "characters": ("characters", "worldview", "creation-standards"),
    "outline": (
        "worldview", "characters", "creation-standards",
        "story-fingerprint-spec", "creative-feature-ledger",
    ),
    "script": (
        "worldview", "characters", "content-spec", "corrections",
        "creation-standards", "golden-sentence-registry",
        "creative-feature-ledger", "prop-registry", "references",
    ),
}


def _recalled(chunk: Chunk, page_types: frozenset, terms: tuple[str, ...]) -> bool:
    if page_type_of(chunk.key) in page_types:
        return True
    return any(term and term in chunk.text for term in terms)


def recall_candidates(
    chunks, *, artifact_type: str, terms: tuple[str, ...] = ()
) -> tuple[Chunk, ...]:
    """Return the soft chunks that may ever receive `exclude_soft`.

    Only chunks this function returns are sent to Jev. Everything else — every
    required chunk and every soft chunk that was not recalled — is kept, so the
    absence of a recall rule can never drop content.
    """

    page_types = frozenset(ARTIFACT_PAGE_TYPES.get(artifact_type, ()))
    return tuple(
        chunk for chunk in chunks
        if not chunk.required and _recalled(chunk, page_types, terms)
    )


def partition(
    chunks, *, artifact_type: str, terms: tuple[str, ...] = ()
) -> tuple[tuple[Chunk, ...], tuple[Chunk, ...]]:
    """Split into (always_kept, candidates), preserving input order.

    Membership is decided by object identity, not by `chunk_id`: ids are built
    from the page key plus the section ordinal, so two chunks can collide, and
    subtracting ids would move a required chunk out of `always_kept` without
    putting it in the candidates — the one way this module could hide a hard
    constraint from the model.
    """

    chunks = tuple(chunks)
    candidates = recall_candidates(chunks, artifact_type=artifact_type, terms=terms)
    candidate_identities = {id(chunk) for chunk in candidates}
    always = tuple(
        chunk for chunk in chunks if id(chunk) not in candidate_identities
    )
    return always, candidates
