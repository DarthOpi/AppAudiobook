"""Domain models used by the text analysis pipeline."""

from dataclasses import dataclass
from typing import Literal

SegmentType = Literal["narration", "dialogue"]


@dataclass(frozen=True, slots=True)
class TextSegment:
    """A fragment of text classified as narration or dialogue."""

    type: SegmentType
    text: str

    def as_dict(self) -> dict[str, str]:
        """Return a JSON-serializable representation of the segment."""
        return {"type": self.type, "text": self.text}

