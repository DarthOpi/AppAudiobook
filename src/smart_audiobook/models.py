"""Domain models used by the text analysis pipeline."""

from dataclasses import dataclass
from typing import Literal

SegmentType = Literal["narration", "dialogue"]
ResolutionSource = Literal["rule", "llm", "unknown"]

NARRATOR = "Narrator"
UNKNOWN_SPEAKER = "Unknown"


@dataclass(frozen=True, slots=True)
class TextSegment:
    """A fragment of text classified as narration or dialogue."""

    type: SegmentType
    text: str
    speaker: str

    def as_dict(self) -> dict[str, str]:
        """Return a JSON-serializable representation of the segment."""
        return {"type": self.type, "speaker": self.speaker, "text": self.text}


@dataclass(frozen=True, slots=True)
class SpeakerResolution:
    """The result of attempting to identify a dialogue speaker."""

    speaker: str
    confidence: float
    source: ResolutionSource


@dataclass(frozen=True, slots=True)
class SpeakerContext:
    """A dialogue plus its nearby segments and known characters."""

    dialogue: TextSegment
    before: tuple[TextSegment, ...]
    after: tuple[TextSegment, ...]
    known_characters: tuple[str, ...]

    def cache_key(self) -> tuple[object, ...]:
        """Return a stable key for identical LLM requests in one execution."""
        return (
            self.dialogue.text,
            tuple(
                (segment.type, segment.speaker, segment.text)
                for segment in self.before
            ),
            tuple(
                (segment.type, segment.speaker, segment.text)
                for segment in self.after
            ),
            self.known_characters,
        )


@dataclass(frozen=True, slots=True)
class SpeakerAnalysis:
    """Speaker-enriched segments and the canonical characters found."""

    segments: tuple[TextSegment, ...]
    characters: tuple[str, ...]
