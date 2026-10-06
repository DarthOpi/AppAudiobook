"""Conservative paragraph reconstruction for native-text novel PDFs (no OCR)."""

import re

from smart_audiobook.chapter_detection import is_chapter_heading

SCENE_BREAK = re.compile(r"(?:\*\s*\*\s*\*|---|§)")


class PdfTextReconstructor:
    """Prefer explicit boundaries; join incomplete visual lines, not sentences.

    Without layout coordinates some paragraph boundaries are inherently ambiguous.
    Blank lines, indentation, headings, scene breaks and new speech/thought openings
    win over reflow. A closing quote must be found before ending a wrapped quote.
    """

    def reconstruct(self, text: str) -> str:
        paragraphs: list[str] = []
        current = ""
        for raw in text.splitlines():
            line = self._heading(raw.rstrip()).strip()
            if not line:
                if current:
                    paragraphs.append(current)
                    current = ""
                continue
            structural = is_chapter_heading(line) or bool(SCENE_BREAK.fullmatch(line))
            new_quote = line.startswith(("—", '"', "“", "«", "'", "‘"))
            unfinished_quote = self._open_quote(current)
            boundary = structural or (new_quote and not unfinished_quote) or (
                not unfinished_quote and (raw.startswith("   ") or bool(re.search(r'[.!?…:][\"”»\'’)]*$', current)))
            )
            if current and boundary:
                paragraphs.append(current)
                current = ""
            if structural:
                paragraphs.append(line)
            elif current:
                current = current[:-1] + line if re.search(r"[a-záéíóúñ]-$", current, re.I) and line[0].islower() else current + " " + line
            else:
                current = line
        if current:
            paragraphs.append(current)
        return "\n\n".join(paragraphs)

    @staticmethod
    def _open_quote(text: str) -> bool:
        # Only an opening delimiter at the paragraph start, never word apostrophes.
        for opening, closing in (("'", "'"), ('"', '"'), ("‘", "’"), ("“", "”"), ("«", "»")):
            if text.startswith(opening):
                return closing not in text[1:]
        return False

    @staticmethod
    def _heading(text: str) -> str:
        if re.match(r"^\s*C\s+a\s+p\s+[ií]\s+t\s+u\s+l\s+o\b", text, re.I):
            return " ".join(re.sub(r"\s+", "", word) for word in re.split(r"\s{2,}", text.strip()))
        return text
