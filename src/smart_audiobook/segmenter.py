"""Rule-based segmentation of narration and Spanish-style dialogue."""

import re
from smart_audiobook.models import NARRATOR, UNKNOWN_SPEAKER, TextSegment

EM_DASH = "—"


def segment_text(text: str) -> list[TextSegment]:
    """Split text into ordered narration and dialogue segments.

    A non-empty line beginning with an em dash is treated as Spanish-style
    dialogue. Subsequent em dashes on that line alternate between narration
    and dialogue. Other lines remain narration.
    """
    segments: list[TextSegment] = []
    scene_break = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.fullmatch(r"(?:\*\s*\*\s*\*|---|§)", line):
            scene_break = True
            continue

        if not line.startswith(EM_DASH):
            segments.append(
                TextSegment(type="narration", text=line, speaker=NARRATOR, scene_break_before=scene_break)
            )
            scene_break = False
            continue

        fragments = line.split(EM_DASH)[1:]
        for index, raw_fragment in enumerate(fragments):
            fragment = raw_fragment.strip()
            if not fragment:
                continue

            segment_type = "dialogue" if index % 2 == 0 else "narration"
            speaker = UNKNOWN_SPEAKER if segment_type == "dialogue" else NARRATOR
            segments.append(
                TextSegment(type=segment_type, text=fragment, speaker=speaker, scene_break_before=scene_break)
            )
            scene_break = False

    return segments
