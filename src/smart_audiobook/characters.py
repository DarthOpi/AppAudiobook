"""Character-name normalization and deduplication."""

import unicodedata

from smart_audiobook.models import NARRATOR, UNKNOWN_SPEAKER


def normalize_character_name(name: str) -> str:
    """Create an accent- and case-insensitive identity key for a name."""
    compact_name = " ".join(name.strip().split())
    decomposed_name = unicodedata.normalize("NFKD", compact_name)
    without_accents = "".join(
        character
        for character in decomposed_name
        if not unicodedata.combining(character)
    )
    return without_accents.casefold()


class CharacterRegistry:
    """Keep one display name for each normalized character identity."""

    def __init__(self) -> None:
        self._display_names: dict[str, str] = {}
        self.register(NARRATOR)

    def register(self, name: str) -> str:
        """Register a name and return its canonical display form."""
        display_name = " ".join(name.strip().split())
        if not display_name:
            return UNKNOWN_SPEAKER

        key = normalize_character_name(display_name)
        if not key or key == normalize_character_name(UNKNOWN_SPEAKER):
            return UNKNOWN_SPEAKER

        return self._display_names.setdefault(key, display_name)

    @property
    def characters(self) -> tuple[str, ...]:
        """Return known characters in first-seen order, including Narrator."""
        return tuple(self._display_names.values())
