"""HTTP tests for the V0.5 web interface with a fake application service."""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from smart_audiobook.application import ProcessingResult
from smart_audiobook.book_processor import AnalyzedChapter, BookAnalysis, BookOutput
from smart_audiobook.document_loaders import DocumentLoadError
from smart_audiobook.models import Chapter, Document, NARRATOR, TextSegment
from smart_audiobook.web import create_app

FIXTURES = Path(__file__).parent / "fixtures"


class FakeApplicationService:
    """Create deterministic fake audio while recording the sanitized upload."""

    def __init__(self) -> None:
        self.received_names: list[str] = []

    def process(
        self,
        source_path: Path,
        output_root: Path,
        use_llm: bool = True,
        full_audiobook_path: Path | None = None,
    ) -> ProcessingResult:
        self.received_names.append(source_path.name)
        self._assert_upload_exists(source_path)
        output_directory = output_root / "sample_book"
        output_directory.mkdir(parents=True, exist_ok=True)
        chapter_audio = output_directory / "01_capitulo_1.wav"
        full_audio = output_directory / "full_audiobook.wav"
        metadata = output_directory / "metadata.json"
        chapter_audio.write_bytes(b"RIFF-fake-chapter")
        full_audio.write_bytes(b"RIFF-fake-full")
        metadata.write_text("{}", encoding="utf-8")

        chapter = Chapter(1, "Capítulo 1", "Capítulo 1\n\nUna historia breve.")
        document = Document(
            title="Sample book",
            source_path=source_path,
            format=source_path.suffix.casefold().lstrip("."),
            full_text=chapter.text,
            chapters=(chapter,),
        )
        analyzed_chapter = AnalyzedChapter(
            number=1,
            title=chapter.title,
            segments=(TextSegment("narration", chapter.text, NARRATOR),),
        )
        analysis = BookAnalysis(
            document=document,
            chapters=(analyzed_chapter,),
            characters=(NARRATOR,),
        )
        output = BookOutput(
            directory=output_directory,
            chapter_files=(chapter_audio,),
            full_audiobook=full_audio,
            metadata=metadata,
            voice_assignments={NARRATOR: "voice-test"},
        )
        return ProcessingResult(analysis=analysis, output=output)

    @staticmethod
    def _assert_upload_exists(source_path: Path) -> None:
        if not source_path.is_file():
            raise AssertionError("The temporary upload must exist during processing")


class FailingApplicationService:
    def process(self, *_args: object, **_kwargs: object) -> ProcessingResult:
        raise DocumentLoadError("El documento de prueba no se pudo leer.")


class WebInterfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.service = FakeApplicationService()
        self.app = create_app(
            service=self.service,
            output_root=Path(self.temporary_directory.name),
        )
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.temporary_directory.cleanup()

    def test_home_page_returns_200(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Smart Audiobook", response.text)
        self.assertIn("TXT, PDF o DOCX", response.text)

    def test_upload_valid_txt(self) -> None:
        response = self._upload("story.txt", b"Capitulo 1\nA short story.")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Sample book", response.text)
        self.assertIn("Capítulo 1", response.text)
        self.assertIn("voice-test", response.text)

    def test_upload_valid_pdf(self) -> None:
        response = self._upload(
            "story.pdf",
            (FIXTURES / "sample_book.pdf").read_bytes(),
            "application/pdf",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.service.received_names[-1], "story.pdf")

    def test_upload_valid_docx(self) -> None:
        response = self._upload(
            "story.docx",
            (FIXTURES / "sample_book.docx").read_bytes(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.service.received_names[-1], "story.docx")

    def test_rejects_unsupported_extension(self) -> None:
        response = self._upload("story.exe", b"not executable")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Formato no soportado", response.text)
        self.assertFalse(self.service.received_names)

    def test_rejects_empty_upload(self) -> None:
        response = self._upload("story.txt", b"")
        self.assertEqual(response.status_code, 400)
        self.assertIn("archivo está vacío", response.text)

    def test_rejects_upload_over_configured_limit(self) -> None:
        app = create_app(
            service=self.service,
            output_root=Path(self.temporary_directory.name),
            max_upload_size=4,
        )
        with TestClient(app) as client:
            response = client.post(
                "/upload",
                files={"document": ("story.txt", b"too large", "text/plain")},
            )
        self.assertEqual(response.status_code, 413)
        self.assertIn("supera el límite", response.text)
        self.assertFalse(self.service.received_names)

    def test_rejects_content_that_does_not_match_extension(self) -> None:
        response = self._upload("fake.pdf", b"plain text")
        self.assertEqual(response.status_code, 400)
        self.assertIn("PDF válido", response.text)

    def test_sanitizes_received_filename_and_discards_directories(self) -> None:
        response = self._upload("../../Mi Libro (final).txt", b"A story")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.service.received_names[-1], "mi_libro_final.txt")

    def test_valid_download_endpoint(self) -> None:
        response = self._upload("story.txt", b"A story")
        job_id = response.url.path.rstrip("/").split("/")[-1]
        download = self.client.get(
            f"/downloads/{job_id}/full_audiobook.wav"
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, b"RIFF-fake-full")
        self.assertIn("attachment", download.headers["content-disposition"])

    def test_path_traversal_is_rejected(self) -> None:
        response = self._upload("story.txt", b"A story")
        job_id = response.url.path.rstrip("/").split("/")[-1]
        traversal = self.client.get(
            f"/downloads/{job_id}/%2E%2E%2Fmetadata.json"
        )
        self.assertEqual(traversal.status_code, 404)

    def test_pipeline_error_is_displayed_without_traceback(self) -> None:
        app = create_app(
            service=FailingApplicationService(),
            output_root=Path(self.temporary_directory.name),
        )
        with TestClient(app) as client:
            response = client.post(
                "/upload",
                files={"document": ("story.txt", b"A story", "text/plain")},
            )
        self.assertEqual(response.status_code, 422)
        self.assertIn("El documento de prueba no se pudo leer", response.text)
        self.assertNotIn("Traceback", response.text)

    def _upload(
        self,
        filename: str,
        content: bytes,
        content_type: str = "text/plain",
    ):
        return self.client.post(
            "/upload",
            files={"document": (filename, content, content_type)},
            follow_redirects=True,
        )


if __name__ == "__main__":
    unittest.main()
