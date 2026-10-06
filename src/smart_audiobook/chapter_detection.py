"""Chapter detection using DOCX styles and conservative heading patterns."""

import re
from dataclasses import replace

from smart_audiobook.models import Chapter, Document, DocumentBlock
from smart_audiobook.text_normalizer import normalize_text

_NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
}
_CHAPTER_PATTERN = re.compile(
    r"^(?:cap[ií]tulo|chapter|episodio|episode|parte|part|volumen|volume)\s+"
    r"(?:\d+|[ivxlcdm]+|"
    + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
    + r")(?:\s*[:.\-–—]\s*|\s+)?(?:.+)?$",
    re.IGNORECASE,
)
_NUMBERED_PATTERN = re.compile(r"^\d+\s*(?:\.|[-–—:])\s+\S.+$")
_SPECIAL_PATTERN = re.compile(r"^(?:pr[oó]logo|ep[ií]logo)$", re.IGNORECASE)


def detect_chapters(document: Document) -> Document:
    """Return a document with chapters inferred from styles or heading text."""
    if document.chapters:
        return document
    blocks = document.blocks or tuple(
        DocumentBlock(text=line)
        for line in document.full_text.splitlines()
        if line.strip()
    )
    heading_indexes = [
        index
        for index, block in enumerate(blocks)
        if block.heading_level is not None or is_chapter_heading(block.text)
    ]

    if not heading_indexes:
        chapter = Chapter(number=1, title="Full document", text=document.full_text, blocks=blocks)
        return replace(document, chapters=(chapter,))

    chapters: list[Chapter] = []
    first_heading = heading_indexes[0]
    preamble = _join_blocks(blocks[:first_heading])
    if preamble:
        chapters.append(Chapter(number=1, title="Introduction", text=preamble, blocks=blocks[:first_heading]))

    for position, heading_index in enumerate(heading_indexes):
        end_index = (
            heading_indexes[position + 1]
            if position + 1 < len(heading_indexes)
            else len(blocks)
        )
        heading = blocks[heading_index].text.strip()
        body = _join_blocks(blocks[heading_index + 1 : end_index])
        spoken_text = normalize_text(f"{heading}\n\n{body}" if body else heading)
        chapters.append(
            Chapter(number=len(chapters) + 1, title=heading, text=spoken_text, blocks=blocks[heading_index:end_index])
        )

    return replace(document, chapters=tuple(chapters))


def is_chapter_heading(text: str) -> bool:
    """Return whether a line matches one of the supported chapter patterns."""
    candidate = " ".join(text.strip().split())
    if re.search(r"\.{3,}\s*\d*\s*$", candidate):
        return False  # Contents entries are not chapter starts.
    candidate = re.sub(r"^#{1,6}\s*", "", candidate).strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1].strip()
    return bool(
        _CHAPTER_PATTERN.fullmatch(candidate)
        or _NUMBERED_PATTERN.fullmatch(candidate)
        or _SPECIAL_PATTERN.fullmatch(candidate)
        or _is_uppercase_heading(candidate)
    )


def _join_blocks(blocks: tuple[DocumentBlock, ...]) -> str:
    return normalize_text("\n\n".join(block.text for block in blocks if block.text))


def _is_uppercase_heading(text: str) -> bool:
    """Recognize short uppercase headings common in novels, conservatively."""
    letters = [character for character in text if character.isalpha()]
    return bool(
        2 <= len(letters)
        and len(text) <= 80
        and len(text.split()) <= 10
        and text == text.upper()
        and not text.startswith(("—", "-"))
        and not text.endswith((".", "!", "?", ";", ","))
    )
