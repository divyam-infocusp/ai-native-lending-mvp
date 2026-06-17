from dataclasses import dataclass


@dataclass(frozen=True)
class RenderedSentence:
    reason_code: str
    sentence: str


@dataclass(frozen=True)
class RenderedExplanation:
    reason_codes: list[str]
    language: str
    text: str
    sentences: list[RenderedSentence]


class MissingTemplateError(Exception):
    """No reviewed template exists for a (reason_code, language) — we never
    free-translate binding text (§16.11)."""


class FaithfulnessError(Exception):
    """Rendered text does not cover exactly the fired reason-code set (§16.1)."""
