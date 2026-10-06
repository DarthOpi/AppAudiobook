"""Rule-based segmentation of narration and Spanish-style dialogue."""

import re
from dataclasses import replace
from smart_audiobook.models import NARRATOR, UNKNOWN_SPEAKER, TextSegment

EM_DASH = "—"
QUOTED = re.compile(r'“([^”]+)”|«([^»]+)»|"([^"\n]+)"|‘([^’]+)’|(?<!\w)\x27([^\x27\n]+)\x27(?!\w)')


def segment_text(text: str) -> list[TextSegment]:
    """Split text into ordered narration and dialogue segments.

    Em-dash or double-quoted speech is dialogue; single-quoted fragments are
    private thoughts. PDF visual wraps must be reconstructed before this step.
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
            cursor = 0
            for match in QUOTED.finditer(line):
                if line[cursor:match.start()].strip():
                    segments.append(TextSegment("narration", line[cursor:match.start()].strip(), NARRATOR, scene_break_before=scene_break))
                    scene_break = False
                kind = "internal_thought" if match.group(4) is not None or match.group(5) is not None else "dialogue"
                segments.append(TextSegment(kind, next(g for g in match.groups() if g is not None).strip(), UNKNOWN_SPEAKER, scene_break_before=scene_break))
                scene_break = False
                cursor = match.end()
            if line[cursor:].strip():
                segments.append(TextSegment("narration", line[cursor:].strip(), NARRATOR, scene_break_before=scene_break))
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

    # Persist compact text context; resolution still uses typed neighboring segments.
    return [replace(s,
        context_before="\n".join(p.text for p in segments[max(0, i-4):i])[-2000:],
        context_after="\n".join(p.text for p in segments[i+1:i+5])[:2000:])
        if s.type in {"dialogue", "internal_thought"} else s
        for i, s in enumerate(segments)]
