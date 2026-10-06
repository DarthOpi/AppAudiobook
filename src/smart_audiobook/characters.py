"""Character-name normalization and deduplication."""

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable

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


@dataclass(slots=True)
class CharacterAlias:
    name: str
    known_from_chapter: int = 1
    source: str = "manual"


@dataclass(slots=True)
class CharacterProfile:
    """Evidence-backed identity and aggregate statistics for one book."""

    id: str
    canonical_name: str
    aliases: list[CharacterAlias] = field(default_factory=list)
    alias_suggestions: list[CharacterAlias] = field(default_factory=list)
    importance: str = "unknown"
    gender: str | None = None
    age_group: str | None = None
    description: str | None = None
    personality_traits: list[str] = field(default_factory=list)
    first_seen_chapter: int | None = None
    last_seen_chapter: int | None = None
    dialogue_count: int = 0
    mention_count: int = 0
    chapters_seen: list[int] = field(default_factory=list)
    activity: list[tuple[int, int]] = field(default_factory=list)
    voice_id: str | None = None
    voice_strategy: str = "generic_pool"
    voice_manual: bool = False
    pending: bool = False
    knowledge: dict[str, int] = field(default_factory=dict)

    @property
    def chapter_count(self) -> int:
        return len(self.chapters_seen)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class CharacterRegistry:
    """Own identities, temporal aliases, statistics and duplicate suggestions."""

    def __init__(self, profiles: Iterable[CharacterProfile] = ()) -> None:
        self.profiles = list(profiles)
        self._next_id = max(
            (int(p.id.removeprefix("char_")) for p in self.profiles), default=0
        ) + 1
        if self.find(NARRATOR) is None:
            self.register(NARRATOR)

    def find(self, name: str, chapter: int | None = None) -> CharacterProfile | None:
        key = normalize_character_name(name)
        for profile in self.profiles:
            if chapter is not None and (profile.first_seen_chapter or 0) > chapter:
                continue
            if normalize_character_name(profile.canonical_name) == key:
                return profile
            if any(
                normalize_character_name(alias.name) == key
                and (chapter is None or alias.known_from_chapter <= chapter)
                for alias in profile.aliases
            ):
                return profile
        return None

    def register(self, name: str, chapter: int = 0, pending: bool = False) -> str:
        name = " ".join(name.strip().split())
        if not name or normalize_character_name(name) == normalize_character_name(UNKNOWN_SPEAKER):
            return UNKNOWN_SPEAKER
        existing = self.find(name, chapter if chapter else None)
        if existing:
            return existing.canonical_name
        # A future alias must not absorb an earlier, distinct identity.
        canonical = next((p for p in self.profiles if normalize_character_name(p.canonical_name) == normalize_character_name(name)), None)
        if canonical:
            if chapter:
                canonical.first_seen_chapter = min(chapter, canonical.first_seen_chapter or chapter)
            return canonical.canonical_name
        profile = CharacterProfile(
            id=f"char_{self._next_id:06d}", canonical_name=name,
            first_seen_chapter=chapter or None, pending=pending,
        )
        self._next_id += 1
        self.profiles.append(profile)
        return name

    @property
    def characters(self) -> tuple[str, ...]:
        return tuple(p.canonical_name for p in self.profiles if not p.pending)

    def add_alias(self, canonical: str, alias: str, chapter: int = 1) -> None:
        profile = self.require(canonical)
        alias = " ".join(alias.strip().split())
        if not alias or chapter < 1:
            raise ValueError("Alias o capítulo de conocimiento inválido.")
        owner = self.find(alias)
        if owner and owner is not profile:
            raise ValueError("Ese alias pertenece a otra identidad; confirma una fusión.")
        if normalize_character_name(alias) == normalize_character_name(profile.canonical_name):
            return
        existing = next((a for a in profile.aliases if normalize_character_name(a.name) == normalize_character_name(alias)), None)
        if existing:
            existing.known_from_chapter = chapter
        else:
            profile.aliases.append(CharacterAlias(alias, chapter))

    def remove_alias(self, canonical: str, alias: str) -> None:
        profile = self.require(canonical)
        profile.aliases = [a for a in profile.aliases if normalize_character_name(a.name) != normalize_character_name(alias)]

    def require(self, name: str) -> CharacterProfile:
        profile = self.find(name)
        if profile is None:
            raise ValueError("El personaje indicado no existe.")
        return profile

    def rename(self, name: str, target: str) -> CharacterProfile:
        profile = self.require(name)
        target = " ".join(target.strip().split())
        if not target:
            raise ValueError("El nombre no puede estar vacío.")
        existing = self.find(target)
        if existing and existing is not profile:
            return self.merge(name, existing.canonical_name)
        old = profile.canonical_name
        profile.canonical_name = target
        if normalize_character_name(old) != normalize_character_name(target):
            self.add_alias(target, old, profile.first_seen_chapter or 1)
        return profile

    def merge(self, source: str, target: str, known_from: int = 1) -> CharacterProfile:
        origin, destination = self.require(source), self.require(target)
        if known_from < 1:
            raise ValueError("El capítulo de conocimiento debe ser positivo.")
        if origin is destination or origin.canonical_name in {NARRATOR, UNKNOWN_SPEAKER} or destination.canonical_name == NARRATOR:
            raise ValueError("Selecciona dos personajes válidos y diferentes.")
        self.profiles.remove(origin)
        self.add_alias(destination.canonical_name, origin.canonical_name, known_from)
        for alias in origin.aliases:
            self.add_alias(destination.canonical_name, alias.name, max(known_from, alias.known_from_chapter))
        if origin.voice_manual and not destination.voice_manual:
            destination.voice_id = origin.voice_id
            destination.voice_manual = True
        elif not destination.voice_id:
            destination.voice_id = origin.voice_id
        destination.pending = False
        if origin.first_seen_chapter:
            destination.first_seen_chapter = min(origin.first_seen_chapter, destination.first_seen_chapter or origin.first_seen_chapter)
        return destination

    def observe(self, segment: Any) -> None:
        chapter = max(1, segment.chapter)
        position = segment.order
        if segment.type == "narration":
            for profile in list(self.profiles):
                pattern = re.compile(re.escape(profile.canonical_name) +
                    r",?\s+(?:también\s+)?(?:conocid[oa]\s+como|llamad[oa])\s+([A-ZÁÉÍÓÚÑ][\w-]*(?:\s+(?:[A-ZÁÉÍÓÚÑ][\w-]*|from|de|la|the)){0,3})")
                match = pattern.search(segment.text)
                if match and not any(a.name == match[1] for a in profile.alias_suggestions):
                    profile.alias_suggestions.append(CharacterAlias(match[1], chapter, "text_evidence"))
        mentioned = self.mentions(segment.text, chapter)
        speaker = self.find(segment.speaker, chapter)
        for profile in self.profiles:
            dialogue = segment.type == "dialogue" and profile is speaker
            mentions = mentioned.count(profile.canonical_name)
            if not dialogue and not mentions:
                continue
            profile.dialogue_count += int(dialogue)
            profile.mention_count += mentions
            if chapter not in profile.chapters_seen:
                profile.chapters_seen.append(chapter)
            profile.first_seen_chapter = min(profile.first_seen_chapter or chapter, chapter)
            profile.last_seen_chapter = max(profile.last_seen_chapter or chapter, chapter)
            profile.activity.append((chapter, position))
            profile.activity = profile.activity[-64:]
            profile.importance = self.calculate_importance(profile)

    def rebuild_statistics(self, segments: Iterable[Any]) -> None:
        for p in self.profiles:
            p.dialogue_count = p.mention_count = 0
            p.chapters_seen = []
            p.activity = []
            p.last_seen_chapter = None
        for segment in segments:
            self.observe(segment)

    @staticmethod
    def calculate_importance(profile: CharacterProfile) -> str:
        if profile.pending:
            return "unknown"
        if profile.dialogue_count >= 30 or (profile.chapter_count >= 5 and profile.dialogue_count >= 10):
            return "major"
        if profile.dialogue_count >= 5 or profile.chapter_count >= 3 or profile.mention_count >= 20:
            return "supporting"
        return "minor" if profile.dialogue_count or profile.mention_count else "unknown"

    def mentions(self, text: str, chapter: int) -> list[str]:
        normalized = normalize_character_name(text)
        result: list[str] = []
        for p in self.profiles:
            if (p.first_seen_chapter or 0) > chapter:
                continue
            names = [p.canonical_name] + [a.name for a in p.aliases if a.known_from_chapter <= chapter]
            patterns = sorted({normalize_character_name(n) for n in names}, key=len, reverse=True)
            pattern = r"(?<!\w)(?:" + "|".join(re.escape(n) for n in patterns) + r")(?!\w)"
            result.extend([p.canonical_name] * len(re.findall(pattern, normalized)))
        return result

    def active_characters(self, chapter: int, order: int, window: int = 12) -> tuple[str, ...]:
        ranked = []
        for p in self.profiles:
            activity = [(ch, pos) for ch, pos in p.activity if ch <= chapter and pos <= order and order - pos <= window]
            if activity and not p.pending and p.canonical_name != NARRATOR:
                ranked.append((max(pos for _, pos in activity), p.canonical_name))
        return tuple(name for _, name in sorted(ranked, reverse=True))

    def duplicate_suggestions(self) -> list[tuple[str, str]]:
        result = []
        profiles = [p for p in self.profiles if p.canonical_name != NARRATOR]
        for index, left in enumerate(profiles):
            for alias in left.alias_suggestions:
                right = self.find(alias.name)
                if right and right is not left:
                    result.append((left.canonical_name, right.canonical_name))
            a = normalize_character_name(left.canonical_name)
            for right in profiles[index + 1:]:
                b = normalize_character_name(right.canonical_name)
                # Title/surname similarity is only a suggestion, never a merge.
                if a in b.split() or b in a.split() or SequenceMatcher(None, a, b).ratio() >= 0.78:
                    result.append((left.canonical_name, right.canonical_name))
        return list(dict.fromkeys(result))

    def to_list(self) -> list[dict[str, Any]]:
        return [p.as_dict() for p in self.profiles]

    @classmethod
    def from_list(cls, values: list[dict[str, Any]]) -> "CharacterRegistry":
        profiles = []
        for value in values:
            data = dict(value)
            data["aliases"] = [CharacterAlias(**a) for a in data.get("aliases", [])]
            data["alias_suggestions"] = [CharacterAlias(**a) for a in data.get("alias_suggestions", [])]
            profiles.append(CharacterProfile(**data))
        return cls(profiles)
