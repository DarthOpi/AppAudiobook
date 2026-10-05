"""Tests for chapter heuristics used by novels and web novels."""

import unittest
from pathlib import Path

from smart_audiobook.chapter_detection import detect_chapters, is_chapter_heading
from smart_audiobook.models import Document


class ChapterDetectionTests(unittest.TestCase):
    def test_supported_novel_heading_patterns(self) -> None:
        headings = (
            "Capítulo 1: La llegada",
            "Capítulo IV",
            "Chapter One - Awakening",
            "1. Introducción",
            "Prólogo",
            "Epílogo",
            "EPISODIO 42 — EL PORTAL",
            "# Chapter 12: Home",
            "3 - Una promesa",
            "LA ÚLTIMA NOCHE",
        )
        for heading in headings:
            with self.subTest(heading=heading):
                self.assertTrue(is_chapter_heading(heading))

    def test_regular_prose_is_not_a_heading(self) -> None:
        self.assertFalse(is_chapter_heading("La noche era fría y silenciosa."))
        self.assertFalse(is_chapter_heading("—NO TE MUEVAS —gritó Ana."))

    def test_document_without_headings_becomes_one_chapter(self) -> None:
        document = Document(
            title="Short story",
            source_path=Path("story.txt"),
            format="txt",
            full_text="Una historia muy breve.",
        )
        result = detect_chapters(document)
        self.assertEqual(len(result.chapters), 1)
        self.assertEqual(result.chapters[0].title, "Full document")


if __name__ == "__main__":
    unittest.main()
