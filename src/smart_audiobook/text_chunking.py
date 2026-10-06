"""Sentence-aware text chunking for local speech engines."""

from __future__ import annotations

import re

SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?…])\s+")


def chunk_text(text: str, max_chars: int = 400) -> tuple[str, ...]:
    """Split on paragraphs/sentences and only fall back to word boundaries."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    for paragraph in paragraphs or [text.strip()]:
        sentences = [part.strip() for part in SENTENCE_BOUNDARY.split(paragraph)]
        current = ""
        for sentence in sentences:
            if not sentence:
                continue
            candidate = f"{current} {sentence}".strip()
            if len(candidate) <= max_chars:
                current = candidate
                continue
            if current:
                chunks.append(current)
            pieces = _split_words(sentence, max_chars)
            chunks.extend(pieces[:-1])
            current = pieces[-1] if pieces else ""
        if current:
            chunks.append(current)
    return tuple(chunks)


def _split_words(text: str, max_chars: int) -> list[str]:
    words = text.split()
    result: list[str] = []
    current = ""
    for word in words:
        if len(word) > max_chars:
            if current:
                result.append(current)
                current = ""
            result.extend(word[index : index + max_chars] for index in range(0, len(word), max_chars))
            continue
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            result.append(current)
            current = word
        else:
            current = candidate
    if current:
        result.append(current)
    return result
