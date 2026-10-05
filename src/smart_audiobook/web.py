"""FastAPI presentation layer for Smart Audiobook."""

import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Protocol
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from smart_audiobook.application import (
    AudiobookApplicationService,
    ProcessingResult,
)
from smart_audiobook.document_loaders import DocumentLoadError
from smart_audiobook.tts import SpeechGenerationError
from smart_audiobook.web_security import (
    MAX_UPLOAD_SIZE,
    UploadValidationError,
    resolve_allowed_output,
    sanitize_upload_name,
    validate_uploaded_content,
)

LOGGER = logging.getLogger(__name__)
PACKAGE_DIRECTORY = Path(__file__).resolve().parent
TEMPLATE_DIRECTORY = PACKAGE_DIRECTORY / "templates"
STATIC_DIRECTORY = PACKAGE_DIRECTORY / "static"


class ProcessingService(Protocol):
    def process(
        self,
        source_path: Path,
        output_root: Path,
        use_llm: bool = True,
        full_audiobook_path: Path | None = None,
    ) -> ProcessingResult:
        """Run the shared application pipeline."""


@dataclass(frozen=True, slots=True)
class WebResult:
    """Controlled, in-memory reference to one generated audiobook."""

    job_id: str
    source_name: str
    processing: ProcessingResult
    allowed_files: dict[str, Path]


class ResultStore:
    """Small thread-safe registry; intentionally non-persistent in V0.5."""

    def __init__(self) -> None:
        self._items: dict[str, WebResult] = {}
        self._lock = Lock()

    def add(self, result: WebResult) -> None:
        with self._lock:
            self._items[result.job_id] = result

    def get(self, job_id: str) -> WebResult | None:
        with self._lock:
            return self._items.get(job_id)


def create_app(
    service: ProcessingService | None = None,
    output_root: Path | None = None,
    max_upload_size: int = MAX_UPLOAD_SIZE,
) -> FastAPI:
    """Create an injectable web application for production and tests."""
    logging.getLogger("smart_audiobook").setLevel(logging.INFO)
    app = FastAPI(title="Smart Audiobook", version="0.5.0")
    templates = Jinja2Templates(directory=str(TEMPLATE_DIRECTORY))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIRECTORY)), name="static")
    app.state.service = service or AudiobookApplicationService()
    app.state.output_root = (output_root or Path("output") / "web").resolve()
    app.state.max_upload_size = max_upload_size
    app.state.results = ResultStore()

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"error": None, "max_upload_mb": max_upload_size // 1024**2},
        )

    @app.post("/upload")
    async def upload_document(request: Request, document: UploadFile):
        temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        job_root: Path | None = None
        processing_succeeded = False
        try:
            safe_name = sanitize_upload_name(document.filename or "")
            LOGGER.info("File received: %s", safe_name)
            temporary_directory = tempfile.TemporaryDirectory(
                prefix="smart-audiobook-upload-"
            )
            upload_path = Path(temporary_directory.name) / safe_name
            await _store_upload(
                document,
                upload_path,
                request.app.state.max_upload_size,
            )
            validate_uploaded_content(upload_path)

            job_id = uuid4().hex
            job_root = request.app.state.output_root / job_id
            processing = await run_in_threadpool(
                request.app.state.service.process,
                upload_path,
                job_root,
            )
            allowed_files = {
                path.name: path
                for path in (
                    *processing.output.chapter_files,
                    processing.output.full_audiobook,
                )
            }
            request.app.state.results.add(
                WebResult(
                    job_id=job_id,
                    source_name=safe_name,
                    processing=processing,
                    allowed_files=allowed_files,
                )
            )
            processing_succeeded = True
            return RedirectResponse(f"/results/{job_id}", status_code=303)
        except UploadValidationError as error:
            LOGGER.warning("Upload rejected: %s", error)
            return _error_response(
                templates,
                request,
                str(error),
                error.status_code,
                max_upload_size,
            )
        except (DocumentLoadError, SpeechGenerationError) as error:
            LOGGER.warning("Document processing failed: %s", error)
            return _error_response(
                templates,
                request,
                str(error),
                422,
                max_upload_size,
            )
        except Exception:
            LOGGER.exception("Unexpected web processing error")
            return _error_response(
                templates,
                request,
                "No se pudo procesar el documento. Revisa el archivo e inténtalo de nuevo.",
                500,
                max_upload_size,
            )
        finally:
            await document.close()
            if temporary_directory is not None:
                temporary_directory.cleanup()
            if job_root is not None and not processing_succeeded:
                _cleanup_failed_job(job_root, request.app.state.output_root)

    @app.get("/results/{job_id}", response_class=HTMLResponse)
    async def result_page(request: Request, job_id: str) -> HTMLResponse:
        stored = request.app.state.results.get(job_id)
        if stored is None:
            raise HTTPException(status_code=404, detail="Resultado no encontrado.")
        result = stored.processing
        chapters = [
            {
                "number": chapter.number,
                "title": chapter.title,
                "filename": audio_path.name,
            }
            for chapter, audio_path in zip(
                result.analysis.chapters,
                result.output.chapter_files,
                strict=True,
            )
        ]
        return templates.TemplateResponse(
            request=request,
            name="result.html",
            context={
                "job_id": job_id,
                "source_name": stored.source_name,
                "result": result,
                "chapters": chapters,
            },
        )

    @app.get("/media/{job_id}/{filename:path}")
    async def play_audio(request: Request, job_id: str, filename: str) -> FileResponse:
        return _serve_result_file(request, job_id, filename, download=False)

    @app.get("/downloads/{job_id}/{filename:path}")
    async def download_audio(
        request: Request,
        job_id: str,
        filename: str,
    ) -> FileResponse:
        return _serve_result_file(request, job_id, filename, download=True)

    return app


async def _store_upload(upload: UploadFile, target: Path, size_limit: int) -> None:
    """Stream one upload to a temporary file with a hard size limit."""
    total_size = 0
    with target.open("wb") as destination:
        while chunk := await upload.read(1024 * 1024):
            total_size += len(chunk)
            if total_size > size_limit:
                raise UploadValidationError(
                    f"El archivo supera el límite de {size_limit // 1024**2} MiB.",
                    status_code=413,
                )
            destination.write(chunk)
    if total_size == 0:
        raise UploadValidationError("El archivo está vacío.")


def _serve_result_file(
    request: Request,
    job_id: str,
    filename: str,
    download: bool,
) -> FileResponse:
    stored = request.app.state.results.get(job_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Resultado no encontrado.")
    path = resolve_allowed_output(
        stored.processing.output.directory,
        filename,
        stored.allowed_files,
    )
    if path is None:
        raise HTTPException(status_code=404, detail="Audio no encontrado.")
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=path.name,
        content_disposition_type="attachment" if download else "inline",
    )


def _error_response(
    templates: Jinja2Templates,
    request: Request,
    message: str,
    status_code: int,
    max_upload_size: int,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "error": message,
            "max_upload_mb": max_upload_size // 1024**2,
        },
        status_code=status_code,
    )


def _cleanup_failed_job(job_root: Path, output_root: Path) -> None:
    """Remove only the controlled UUID directory of a failed web job."""
    resolved_root = output_root.resolve()
    resolved_job = job_root.resolve()
    if resolved_job.parent == resolved_root:
        shutil.rmtree(resolved_job, ignore_errors=True)


app = create_app()
