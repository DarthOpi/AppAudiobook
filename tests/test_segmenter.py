"""Tests for narration and dialogue segmentation."""

import unittest

from smart_audiobook.models import TextSegment
from smart_audiobook.segmenter import segment_text


class SegmentTextTests(unittest.TestCase):
    def test_text_with_only_narration(self) -> None:
        self.assertEqual(
            segment_text("Pedro entró en la habitación."),
            [TextSegment(type="narration", text="Pedro entró en la habitación.")],
        )

    def test_line_with_only_dialogue(self) -> None:
        self.assertEqual(
            segment_text("—Hola, María."),
            [TextSegment(type="dialogue", text="Hola, María.")],
        )

    def test_dialogue_followed_by_attribution(self) -> None:
        self.assertEqual(
            segment_text("—Hola —dijo María."),
            [
                TextSegment(type="dialogue", text="Hola"),
                TextSegment(type="narration", text="dijo María."),
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
                TextSegment(type="narration", text="Pedro entró en la habitación."),
                TextSegment(type="dialogue", text="¿Dónde estabas?"),
                TextSegment(type="narration", text="preguntó María."),
                TextSegment(type="dialogue", text="Trabajando"),
                TextSegment(type="narration", text="respondió Pedro."),
                TextSegment(type="narration", text="María cerró la puerta."),
            ],
        )

    def test_empty_text_returns_no_segments(self) -> None:
        self.assertEqual(segment_text("  \n\n"), [])


if __name__ == "__main__":
    unittest.main()

