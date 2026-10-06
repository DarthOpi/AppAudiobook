"""HTTP tests for the V0.5 web interface with a fake application service."""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from smart_audiobook.application import ProcessingResult
from smart_audiobook.book_processor import AnalyzedChapter, BookAnalysis, BookOutput
from smart_audiobook.document_loaders import DocumentLoadError
from smart_audiobook.models import (
    Chapter,
    Document,
    NARRATOR,
    TextSegment,
    UNKNOWN_SPEAKER,
)
from smart_audiobook.tts_providers import VoiceInfo
from smart_audiobook.web import create_app

FIXTURES = Path(__file__).parent / "fixtures"


class FakeApplicationService:
    """Provide deterministic analysis and generation without real TTS."""

    def __init__(self, dialogue_count: int = 1) -> None:
        self.received_names: list[str] = []
        self.generated = False
        self.dialogue_count = dialogue_count

    def analyze(
        self,
        source_path: Path,
        use_llm: bool = True,
    ) -> BookAnalysis:
        self.received_names.append(source_path.name)
        self._assert_upload_exists(source_path)
        chapter = Chapter(1, "Capítulo 1", "Capítulo 1\n\nUna historia breve.")
        document = Document(
            title="Sample book",
            source_path=source_path,
            format=source_path.suffix.casefold().lstrip("."),
            full_text=chapter.text,
            chapters=(chapter,),
        )
        segments = [
            TextSegment(
                "narration",
                chapter.text,
                NARRATOR,
                id="seg_000001",
                chapter=1,
                order=1,
                confidence=1.0,
                resolution_method="system",
            )
        ]
        segments.extend(
            TextSegment(
                "dialogue",
                f"Diálogo número {index}.",
                UNKNOWN_SPEAKER,
                id=f"seg_{index + 1:06d}",
                chapter=1,
                order=index + 1,
                resolution_method="unknown",
            )
            for index in range(1, self.dialogue_count + 1)
        )
        analyzed_chapter = AnalyzedChapter(
            number=1,
            title=chapter.title,
            segments=tuple(segments),
        )
        return BookAnalysis(
            document=document,
            chapters=(analyzed_chapter,),
            characters=(NARRATOR,),
        )

    def generate(
        self,
        analysis: BookAnalysis,
        output_root: Path,
        voices_by_speaker: dict[str, str] | None = None,
        tts_provider: object | None = None,
    ) -> ProcessingResult:
        self.generated = True
        output_directory = output_root / "sample_book"
        output_directory.mkdir(parents=True, exist_ok=True)
        chapter_audio = output_directory / "01_capitulo_1.wav"
        full_audio = output_directory / "full_audiobook.wav"
        metadata = output_directory / "metadata.json"
        chapter_audio.write_bytes(b"RIFF-fake-chapter")
        full_audio.write_bytes(b"RIFF-fake-full")
        metadata.write_text("{}", encoding="utf-8")
        output = BookOutput(
            directory=output_directory,
            chapter_files=(chapter_audio,),
            full_audiobook=full_audio,
            metadata=metadata,
            voice_assignments=dict(voices_by_speaker or {}),
        )
        return ProcessingResult(analysis=analysis, output=output)

    @staticmethod
    def _assert_upload_exists(source_path: Path) -> None:
        if not source_path.is_file():
            raise AssertionError("The temporary upload must exist during processing")


class FailingApplicationService:
    def analyze(self, *_args: object, **_kwargs: object) -> BookAnalysis:
        raise DocumentLoadError("El documento de prueba no se pudo leer.")


class FakeTTSProvider:
    def __init__(self) -> None:
        self.calls = 0

    def list_voices(self) -> list[VoiceInfo]:
        return [VoiceInfo("voice-test", "voice-test", "es-ES", None)]

    def synthesize(self, text: str, output_path: Path, voice_id: str) -> Path:
        self.calls += 1
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"RIFF-fake-preview")
        return output_path


class WebInterfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.service = FakeApplicationService()
        self.tts_provider = FakeTTSProvider()
        self.app = create_app(
            service=self.service,
            output_root=Path(self.temporary_directory.name),
            tts_provider=self.tts_provider,
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
        self.assertFalse(self.service.generated)

    def test_upload_creates_processing_id_and_persisted_analysis(self) -> None:
        response = self._upload("story.txt", b"A story")
        processing_id = response.url.path.rstrip("/").split("/")[-1]
        self.assertRegex(processing_id, r"^[0-9a-f]{32}$")
        self.assertTrue(
            (
                Path(self.temporary_directory.name)
                / processing_id
                / "analysis.json"
            ).is_file()
        )

    def test_review_reload_reuses_existing_analysis(self) -> None:
        response = self._upload("story.txt", b"A story")
        self.client.get(response.url.path)
        self.client.get(response.url.path)
        self.assertEqual(len(self.service.received_names), 1)

    def test_pending_generation_page_renders_json_urls(self) -> None:
        uploaded = self._upload("story.txt", b"A story")
        job_id = uploaded.url.path.rstrip("/").split("/")[-1]

        response = self.client.get(f"/generation/{job_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f'const endpoint = "http://testserver/generation/{job_id}/status";',
            response.text,
        )
        self.assertIn(
            f'const resultUrl = "http://testserver/results/{job_id}";',
            response.text,
        )
        self.assertFalse(self.service.generated)

    def test_completed_generation_redirects_to_playable_result(self) -> None:
        uploaded = self._upload("story.txt", b"A story")
        job_id = uploaded.url.path.rstrip("/").split("/")[-1]
        self.client.post(f"/review/{job_id}/generate", follow_redirects=False)

        response = self.client.get(f"/generation/{job_id}", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        result = self.client.get(response.headers["location"])
        self.assertEqual(result.status_code, 200)
        self.assertIn(f"/media/{job_id}/full_audiobook.wav", result.text)
        self.assertIn(f"/downloads/{job_id}/full_audiobook.wav", result.text)
        status = self.client.get(f"/generation/{job_id}/status")
        self.assertTrue(status.json()["result_ready"])

    def test_complete_review_flow_uses_manual_changes(self) -> None:
        uploaded = self._upload("story.txt", b"A story")
        processing_id = uploaded.url.path.rstrip("/").split("/")[-1]

        saved = self.client.post(
            f"/review/{processing_id}/segments",
            data={
                "speaker_seg_000002": "__new__",
                "new_seg_000002": "Sunny",
            },
            follow_redirects=True,
        )
        self.assertEqual(saved.status_code, 200)
        self.assertIn("Sunny", saved.text)

        voice = self.client.post(
            f"/review/{processing_id}/voices",
            data={"character": "Sunny", "voice_id": "voice-test"},
            follow_redirects=True,
        )
        self.assertEqual(voice.status_code, 200)

        preview = self.client.post(
            f"/review/{processing_id}/preview",
            data={"voice_id": "voice-test"},
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.content, b"RIFF-fake-preview")

        generated = self.client.post(
            f"/review/{processing_id}/generate",
            follow_redirects=True,
        )
        self.assertEqual(generated.status_code, 200)
        self.assertIn("AUDIOBOOK READY", generated.text)
        self.assertTrue(self.service.generated)

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
            tts_provider=FakeTTSProvider(),
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
        generated = self.client.post(
            f"/review/{job_id}/generate",
            follow_redirects=True,
        )
        self.assertEqual(generated.status_code, 200)
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

    def test_invalid_processing_id_returns_controlled_error(self) -> None:
        response = self.client.get("/review/not-a-valid-id")
        self.assertEqual(response.status_code, 404)
        self.assertIn("Identificador de procesamiento no válido", response.text)

    def test_unknown_filter_and_pagination(self) -> None:
        service = FakeApplicationService(dialogue_count=55)
        app = create_app(
            service=service,
            output_root=Path(self.temporary_directory.name) / "paged",
            tts_provider=FakeTTSProvider(),
        )
        with TestClient(app) as client:
            upload = client.post(
                "/upload",
                files={"document": ("story.txt", b"A story", "text/plain")},
                follow_redirects=True,
            )
            processing_id = upload.url.path.rstrip("/").split("/")[-1]
            filtered = client.get(
                f"/review/{processing_id}?speaker=unknown&chapter=&page=2"
            )
        self.assertEqual(filtered.status_code, 200)
        self.assertIn("55 resultados", filtered.text)
        self.assertIn("Página 2 de 2", filtered.text)
        self.assertIn("Diálogo número 55", filtered.text)

    def test_pipeline_error_is_displayed_without_traceback(self) -> None:
        app = create_app(
            service=FailingApplicationService(),
            output_root=Path(self.temporary_directory.name),
            tts_provider=FakeTTSProvider(),
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
