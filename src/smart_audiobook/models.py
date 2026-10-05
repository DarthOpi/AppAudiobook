"""Domain models used by the text analysis pipeline."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

SegmentType = Literal["narration", "dialogue"]
ResolutionSource = Literal["rule", "llm", "manual", "system", "unknown"]
DocumentFormat = Literal["txt", "pdf", "docx"]

NARRATOR = "Narrator"
UNKNOWN_SPEAKER = "Unknown"


@dataclass(frozen=True, slots=True)
class DocumentBlock:
    """A normalized block extracted from a source document."""

    text: str
    heading_level: int | None = None


@dataclass(frozen=True, slots=True)
class Chapter:
    """A logical, ordered section of a document."""

    number: int
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class Document:
    """Format-independent representation used by the audiobook pipeline."""

    title: str
    source_path: Path
    format: DocumentFormat
    full_text: str
    chapters: tuple[Chapter, ...] = ()
    blocks: tuple[DocumentBlock, ...] = ()


@dataclass(frozen=True, slots=True)
class TextSegment:
    """A fragment of text classified as narration or dialogue."""

    type: SegmentType
    text: str
    speaker: str
    id: str = ""
    chapter: int = 0
    order: int = 0
    confidence: float | None = None
    resolution_method: ResolutionSource | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of the segment."""
        return {
            "id": self.id,
            "chapter": self.chapter,
            "order": self.order,
            "type": self.type,
            "text": self.text,
            "speaker": self.speaker,
            "confidence": self.confidence,
            "resolution_method": self.resolution_method,
        }


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
