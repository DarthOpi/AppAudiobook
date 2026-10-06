"""Domain models used by the text analysis pipeline."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

SegmentType = Literal["narration", "dialogue", "internal_thought", "system_message"]
ResolutionSource = Literal["rule", "conversation", "candidate", "llm", "manual", "system", "unknown"]
DocumentFormat = Literal["txt", "pdf", "docx", "epub"]


class ChapterStatus(str, Enum):
    NOT_ANALYZED = "not_analyzed"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    REVIEW_REQUIRED = "review_required"
    READY_FOR_AUDIO = "ready_for_audio"
    GENERATING_AUDIO = "generating_audio"
    COMPLETED = "completed"
    FAILED = "failed"

NARRATOR = "Narrator"
UNKNOWN_SPEAKER = "Unknown"


@dataclass(frozen=True, slots=True)
class DocumentBlock:
    """A normalized block extracted from a source document."""

    text: str
    heading_level: int | None = None
    emphasis: tuple[str, ...] = ()
    scene_break: bool = False
    source_reference: str | None = None


@dataclass(frozen=True, slots=True)
class Chapter:
    """A logical, ordered section of a document."""

    number: int
    title: str
    text: str
    id: str = ""
    source_reference: str | None = None
    raw_text: str | None = None
    blocks: tuple[DocumentBlock, ...] = ()
    status: ChapterStatus = ChapterStatus.NOT_ANALYZED
    selected_for_processing: bool = True
    narrate: bool = True
    section_kind: str | None = None
    word_count: int = 0
    text_path: Path | None = field(default=None, repr=False, compare=False)
    consistency_warning: str | None = None
    cleaning_notes: tuple[str, ...] = ()

    def read_text(self) -> str:
        """Load only this chapter's normalized text when persisted separately."""
        if self.text_path is not None:
            import json
            return str(json.loads(self.text_path.read_text(encoding="utf-8"))["normalized_text"])
        return self.text


@dataclass(frozen=True, slots=True)
class Document:
    """Format-independent representation used by the audiobook pipeline."""

    title: str
    source_path: Path
    format: DocumentFormat
    full_text: str
    chapters: tuple[Chapter, ...] = ()
    blocks: tuple[DocumentBlock, ...] = ()
    author: str | None = None
    language: str | None = None
    publisher: str | None = None
    identifier: str | None = None

    @property
    def chapter_count(self) -> int:
        return len(self.chapters)


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
    review_needed: bool = False
    new_character_candidate: str | None = None
    emphasis: tuple[str, ...] = ()
    scene_break_before: bool = False
    context_before: str = field(default="", compare=False)
    context_after: str = field(default="", compare=False)

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
            "review_needed": self.review_needed,
            "new_character_candidate": self.new_character_candidate,
            "emphasis": list(self.emphasis),
            "scene_break_before": self.scene_break_before,
            "context_before": self.context_before,
            "context_after": self.context_after,
        }


@dataclass(frozen=True, slots=True)
class SpeakerResolution:
    """The result of attempting to identify a dialogue speaker."""

    speaker: str
    confidence: float
    source: ResolutionSource
    is_new_character: bool = False


@dataclass(frozen=True, slots=True)
class SpeakerContext:
    """A dialogue plus its nearby segments and known characters."""

    dialogue: TextSegment
    before: tuple[TextSegment, ...]
    after: tuple[TextSegment, ...]
    known_characters: tuple[str, ...]
    candidate_speakers: tuple[str, ...] = ()
    active_characters: tuple[str, ...] = ()
    previous_speakers: tuple[str, ...] = ()
    chapter: int = 0
    candidate_aliases: tuple[tuple[str, str, int], ...] = ()

    def cache_key(self) -> tuple[object, ...]:
        """Return a stable key for identical LLM requests in one execution."""
        return (
            self.dialogue.text,
            self.dialogue.type,
            tuple(
                (segment.type, segment.speaker, segment.text)
                for segment in self.before
            ),
            tuple(
                (segment.type, segment.speaker, segment.text)
                for segment in self.after
            ),
            self.known_characters,
            self.candidate_speakers,
            self.active_characters,
            self.previous_speakers,
            self.chapter,
            self.candidate_aliases,
        )


@dataclass(frozen=True, slots=True)
class SpeakerAnalysis:
    """Speaker-enriched segments and the canonical characters found."""

    segments: tuple[TextSegment, ...]
    characters: tuple[str, ...]
