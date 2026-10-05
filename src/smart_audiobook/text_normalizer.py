"""Conservative text normalization shared by all document loaders."""

import re
import unicodedata


def normalize_text(text: str) -> str:
    """Normalize encoding and whitespace without flattening paragraph structure."""
    normalized = unicodedata.normalize("NFC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u00a0", " ")
    normalized = normalized.replace("\ufeff", "").replace("\u200b", "")
    normalized = normalized.replace("\u2015", "—")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n[ \t]+", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()
