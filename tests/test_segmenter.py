"""Tests for narration and dialogue segmentation."""

import unittest

from smart_audiobook.models import NARRATOR, UNKNOWN_SPEAKER, TextSegment
from smart_audiobook.segmenter import segment_text


class SegmentTextTests(unittest.TestCase):
    def test_text_with_only_narration(self) -> None:
        self.assertEqual(
            segment_text("Pedro entró en la habitación."),
            [
                TextSegment(
                    type="narration",
                    text="Pedro entró en la habitación.",
                    speaker=NARRATOR,
                )
            ],
        )

    def test_line_with_only_dialogue(self) -> None:
        self.assertEqual(
            segment_text("—Hola, María."),
            [
                TextSegment(
                    type="dialogue",
                    text="Hola, María.",
                    speaker=UNKNOWN_SPEAKER,
                )
            ],
        )

    def test_dialogue_followed_by_attribution(self) -> None:
        self.assertEqual(
            segment_text("—Hola —dijo María."),
            [
                TextSegment(
                    type="dialogue", text="Hola", speaker=UNKNOWN_SPEAKER
                ),
                TextSegment(
                    type="narration", text="dijo María.", speaker=NARRATOR
                ),
            ],
        )

    def test_multiple_lines_keep_their_original_order(self) -> None:
        text = """Pedro entró en la habitación.

—¿Dónde estabas? —preguntó María.
—Trabajando —respondió Pedro.
María cerró la puerta."""

        self.assertEqual(
            segment_text(text),
            [
                TextSegment(
                    type="narration",
                    text="Pedro entró en la habitación.",
                    speaker=NARRATOR,
                ),
                TextSegment(
                    type="dialogue",
                    text="¿Dónde estabas?",
                    speaker=UNKNOWN_SPEAKER,
                ),
                TextSegment(
                    type="narration", text="preguntó María.", speaker=NARRATOR
                ),
                TextSegment(
                    type="dialogue", text="Trabajando", speaker=UNKNOWN_SPEAKER
                ),
                TextSegment(
                    type="narration", text="respondió Pedro.", speaker=NARRATOR
                ),
                TextSegment(
                    type="narration",
                    text="María cerró la puerta.",
                    speaker=NARRATOR,
                ),
            ],
        )

    def test_empty_text_returns_no_segments(self) -> None:
        self.assertEqual(segment_text("  \n\n"), [])


if __name__ == "__main__":
    unittest.main()
