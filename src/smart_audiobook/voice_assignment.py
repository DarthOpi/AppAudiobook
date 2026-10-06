"""Automatic character-to-voice assignment."""

from collections.abc import Sequence

from smart_audiobook.models import NARRATOR, TextSegment
from smart_audiobook.characters import CharacterRegistry
from smart_audiobook.tts_providers import VoiceInfo


def assign_voices(
    segments: Sequence[TextSegment],
    voice_ids: Sequence[str],
) -> dict[str, str]:
    """Assign voices by first appearance, reusing them cyclically if needed."""
    if not voice_ids:
        return {}

    speakers = [NARRATOR]
    for segment in segments:
        if segment.speaker not in speakers:
            speakers.append(segment.speaker)

    return {
        speaker: voice_ids[index % len(voice_ids)]
        for index, speaker in enumerate(speakers)
    }


def assign_profile_voices(registry: CharacterRegistry, voices: Sequence[VoiceInfo],
                          existing: dict[str, str] | None = None) -> dict[str, str]:
    """Deterministically reserve voices for recurring cast; preserve choices."""
    assignments = dict(existing or {})
    if not voices:
        return assignments
    rank = {"major": 0, "supporting": 1, "minor": 2, "unknown": 3}
    profiles = sorted((p for p in registry.profiles if not p.pending),
                      key=lambda p: (-1 if p.canonical_name == NARRATOR else rank[p.importance], p.id))
    reserved: set[str] = set()
    for p in profiles:
        if p.canonical_name in assignments:
            p.voice_id = assignments[p.canonical_name]
            if p.voice_strategy == "dedicated" or p.voice_manual:
                reserved.add(p.voice_id)
            continue
        compatible = [v for v in voices if p.gender and v.gender and v.gender.casefold() == p.gender.casefold()]
        candidates = compatible or list(voices)
        dedicated = p.canonical_name == NARRATOR or p.importance in {"major", "supporting"}
        unused = [v for v in candidates if v.id not in reserved]
        if dedicated and unused:
            chosen = unused[0]
            reserved.add(chosen.id)
            p.voice_strategy = "dedicated"
        else:
            pool = [v for v in candidates if v.id not in reserved] or candidates
            chosen = pool[int(p.id.removeprefix("char_")) % len(pool)]
            p.voice_strategy = "generic_pool"
        p.voice_id = chosen.id
        assignments[p.canonical_name] = chosen.id
    return assignments
