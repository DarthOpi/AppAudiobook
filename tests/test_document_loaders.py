"""Integration tests for local TXT, PDF and DOCX fixtures."""

import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter

from smart_audiobook.document_loaders import DocumentLoadError, load_document

FIXTURES = Path(__file__).parent / "fixtures"


class DocumentLoaderTests(unittest.TestCase):
    def test_loads_txt_and_detects_chapters(self) -> None:
        document = load_document(FIXTURES / "sample_book.txt")
        self.assertEqual(document.format, "txt")
        self.assertEqual(len(document.chapters), 5)
        self.assertIn("Ya estamos aquí", document.full_text)

    def test_loads_pdf_with_native_text(self) -> None:
        document = load_document(FIXTURES / "sample_book.pdf")
        self.assertEqual(document.format, "pdf")
        self.assertGreaterEqual(len(document.chapters), 2)
        self.assertIn("Elena encontró una carta", document.full_text)

    def test_loads_docx_and_uses_heading_styles(self) -> None:
        document = load_document(FIXTURES / "sample_book.docx")
        self.assertEqual(document.format, "docx")
        self.assertEqual(document.title, "El faro perdido")
        self.assertEqual([chapter.title for chapter in document.chapters], [
            "Prólogo",
            "Capítulo 1: La señal",
            "Capítulo 2: El regreso",
        ])

    def test_rejects_empty_txt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            empty_file = Path(directory) / "empty.txt"
            empty_file.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(DocumentLoadError, "vacío"):
                load_document(empty_file)

    def test_rejects_unsupported_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "book.rtf"
            source.write_text("text", encoding="utf-8")
            with self.assertRaisesRegex(DocumentLoadError, "Formato no soportado"):
                load_document(source)

    def test_explains_that_image_only_pdf_needs_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scan.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with source.open("wb") as output:
                writer.write(output)
            with self.assertRaisesRegex(DocumentLoadError, "OCR"):
                load_document(source)

    def test_rejects_corrupt_pdf_and_docx(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for filename in ("broken.pdf", "broken.docx"):
                with self.subTest(filename=filename):
                    source = Path(directory) / filename
                    source.write_bytes(b"not a real document")
                    with self.assertRaises(DocumentLoadError):
                        load_document(source)

    def test_rejects_password_protected_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "protected.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            writer.encrypt("secret")
            with source.open("wb") as output:
                writer.write(output)
            with self.assertRaisesRegex(DocumentLoadError, "protegido"):
                load_document(source)

    def test_pdf_with_unusual_blank_page_is_still_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "mixed-pages.pdf"
            writer = PdfWriter()
            writer.append(FIXTURES / "sample_book.pdf")
            writer.add_blank_page(width=100, height=200)
            with source.open("wb") as output:
                writer.write(output)
            document = load_document(source)
            self.assertIn("Capítulo 1", document.full_text)


if __name__ == "__main__":
    unittest.main()
