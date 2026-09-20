"""Lexile measurement boundary. A real client must never estimate a score."""

from dataclasses import dataclass


@dataclass(frozen=True)
class LexileResult:
    score: int
    band: str
    measured: bool


class FakeLexileClient:
    def __init__(self, score: int, band: str) -> None:
        if score < 0:
            raise ValueError("Lexile score must be non-negative")
        self.score = score
        self.band = band

    def measure(self, text: str) -> LexileResult:
        return LexileResult(self.score, self.band, measured=True)
