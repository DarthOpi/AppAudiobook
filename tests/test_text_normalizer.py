"""Tests for normalization and portable output naming."""

import unittest

from smart_audiobook.output_files import safe_filename
from smart_audiobook.text_normalizer import normalize_text


class TextNormalizationTests(unittest.TestCase):
    def test_preserves_paragraph_and_dialogue_boundaries(self) -> None:
        source = "  Primer párrafo.  \r\n\r\n—Hola.\r\n\r\n\r\nSegundo párrafo.  "
        self.assertEqual(
            normalize_text(source),
            "Primer párrafo.\n\n—Hola.\n\nSegundo párrafo.",
        )

    def test_safe_filename_removes_accents_and_reserved_characters(self) -> None:
        self.assertEqual(
            safe_filename('Capítulo 01: ¿Qué pasó? <final>'),
            "capitulo_01_que_paso_final",
        )

    def test_removes_invisible_unicode_and_normalizes_horizontal_bar(self) -> None:
        self.assertEqual(normalize_text("\ufeff\u200b―Hola"), "—Hola")


if __name__ == "__main__":
    unittest.main()
