"""Deterministic baseline-slot resolution for the editorial workflow."""

from dataclasses import dataclass


SLOTS = ("pre_create", "in_create", "post_create", "pre_output", "quality")
ARTIFACT_TYPES = ("positioning", "topic_plan", "worldview", "characters", "outline", "script")


@dataclass(frozen=True)
class SlotRequest:
    artifact_type: str
    series_id: str | None = None
    project_id: str | None = None

    def __post_init__(self) -> None:
        if self.artifact_type not in ARTIFACT_TYPES:
            raise ValueError(f"invalid artifact_type: {self.artifact_type}")


def resolve_slot(request: SlotRequest, slot: str) -> str:
    if slot not in SLOTS:
        raise ValueError(f"invalid slot: {slot}")
    # Phase 3 intentionally installs only the baseline tier. Project and series
    # tiers are added later without changing this return contract.
    return f"{slot}-baseline"
