"""Tests for text file reading and validation."""

import tempfile
import unittest
from pathlib import Path

from smart_audiobook.text_reader import TextFileError, read_text_file


class ReadTextFileTests(unittest.TestCase):
    def test_reads_utf8_text_and_trims_outer_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "book.txt"
            path.write_text("  Érase una vez...  \n", encoding="utf-8")

            self.assertEqual(read_text_file(path), "Érase una vez...")

    def test_rejects_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.txt"
            path.write_text("   \n", encoding="utf-8")

            with self.assertRaisesRegex(TextFileError, "vacío"):
                read_text_file(path)

    def test_rejects_non_txt_file(self) -> None:
        with self.assertRaisesRegex(TextFileError, "extensión .txt"):
            read_text_file(Path("book.pdf"))


if __name__ == "__main__":
    unittest.main()

