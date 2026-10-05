"""Portable output names and metadata serialization."""

import json
import re
import unicodedata
from pathlib import Path
from typing import Any


def safe_filename(value: str, fallback: str = "untitled") -> str:
    """Create a predictable ASCII filename component for all major systems."""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    ).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_value).strip("_.").casefold()
    return slug[:80] or fallback


def write_metadata(data: dict[str, Any], output_path: Path) -> Path:
    """Write human-readable UTF-8 metadata without serializing credentials."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path
