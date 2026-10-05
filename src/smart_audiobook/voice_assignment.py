"""Automatic character-to-voice assignment."""

from collections.abc import Sequence

from smart_audiobook.models import NARRATOR, TextSegment


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
