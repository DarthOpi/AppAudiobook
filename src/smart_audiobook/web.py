"""FastAPI presentation layer for Smart Audiobook."""

import logging
import tempfile
from math import ceil
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from smart_audiobook.application import (
    AudiobookApplicationService,
    ProcessingResult,
)
from smart_audiobook.characters import normalize_character_name
from smart_audiobook.character_config import CharacterConfig
from smart_audiobook.document_loaders import DocumentLoadError
from smart_audiobook.review_service import ReviewService
from smart_audiobook.review_store import (
    ReviewProject,
    ReviewProjectStore,
    ReviewStateError,
)
from smart_audiobook.tts import SpeechGenerationError
from smart_audiobook.tts_config import build_tts_provider
from smart_audiobook.tts_providers import TTSProvider
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
PAGE_SIZE = 50


def create_app(
    service: AudiobookApplicationService | None = None,
    output_root: Path | None = None,
    max_upload_size: int = MAX_UPLOAD_SIZE,
    tts_provider: TTSProvider | None = None,
    review_service: ReviewService | None = None,
) -> FastAPI:
    """Create an injectable web application for production and tests."""
    logging.getLogger("smart_audiobook").setLevel(logging.INFO)
    app = FastAPI(title="Smart Audiobook", version="0.8.0")
    templates = Jinja2Templates(directory=str(TEMPLATE_DIRECTORY))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIRECTORY)), name="static")
    work_root = (output_root or Path("work")).resolve()
    provider = tts_provider or build_tts_provider()
    app.state.review_service = review_service or ReviewService(
        ReviewProjectStore(work_root),
        provider,
        service or AudiobookApplicationService(),
    )
    app.state.max_upload_size = max_upload_size

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
            project = await run_in_threadpool(
                request.app.state.review_service.start_analysis,
                upload_path,
                safe_name,
            )
            return RedirectResponse(
                request.url_for(
                    "review_page", processing_id=project.processing_id
                ),
                status_code=303,
            )
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
            LOGGER.warning("Document analysis failed: %s", error)
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
                "No se pudo analizar el documento. Revisa el archivo.",
                500,
                max_upload_size,
            )
        finally:
            await document.close()
            if temporary_directory is not None:
                temporary_directory.cleanup()

    @app.get("/review/{processing_id}", response_class=HTMLResponse)
    async def review_page(
        request: Request,
        processing_id: str,
        page: int = Query(1, ge=1),
        speaker: str = "all",
        issue: str = "all",
        chapter: str | None = None,
    ) -> HTMLResponse:
        project = _load_or_404(request, processing_id)
        try:
            chapter_number = int(chapter) if chapter else None
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Capítulo no válido.") from error
        return _review_response(
            templates,
            request,
            project,
            page=page,
            speaker=speaker,
            chapter=chapter_number,
            issue=issue,
        )

    @app.post("/review/{processing_id}/characters/aliases")
    async def edit_alias(request: Request, processing_id: str):
        _load_or_404(request, processing_id)
        form = await request.form()
        try:
            await run_in_threadpool(request.app.state.review_service.edit_alias,
                processing_id, str(form.get("character", "")), str(form.get("alias", "")),
                int(str(form.get("known_from", "1"))), form.get("action") == "remove")
        except (ReviewStateError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _review_redirect(request, processing_id)

    @app.post("/review/{processing_id}/reanalyze")
    async def reanalyze(request: Request, processing_id: str):
        _load_or_404(request, processing_id)
        form = await request.form()
        selected = set(str(v) for v in form.getlist("segment_ids")) or None
        try:
            await run_in_threadpool(request.app.state.review_service.reanalyze,
                processing_id, selected, form.get("scope") == "issues")
        except (ReviewStateError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _review_redirect(request, processing_id)

    @app.post("/review/{processing_id}/characters/enrich")
    async def enrich_character(request: Request, processing_id: str):
        _load_or_404(request, processing_id)
        form = await request.form()
        try:
            await run_in_threadpool(request.app.state.review_service.enrich_character,
                processing_id, str(form.get("character", "")), int(str(form.get("chapter", "1"))))
        except (ReviewStateError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _review_redirect(request, processing_id)

    @app.post("/review/{processing_id}/segments")
    async def save_segments(request: Request, processing_id: str):
        project = _load_or_404(request, processing_id)
        form = await request.form()
        updates: dict[str, str] = {}
        valid_ids = {segment.id for segment in project.dialogues}
        for key, value in form.multi_items():
            if not key.startswith("speaker_"):
                continue
            segment_id = key.removeprefix("speaker_")
            if segment_id not in valid_ids:
                raise HTTPException(status_code=400, detail="Segmento no válido.")
            selected = str(value)
            if selected == "__new__":
                selected = str(form.get(f"new_{segment_id}", ""))
            updates[segment_id] = selected
        try:
            await run_in_threadpool(
                request.app.state.review_service.update_speakers,
                processing_id,
                updates,
            )
        except ReviewStateError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _review_redirect(request, processing_id)

    @app.post("/review/{processing_id}/characters/rename")
    async def rename_character(request: Request, processing_id: str):
        _load_or_404(request, processing_id)
        form = await request.form()
        try:
            await run_in_threadpool(
                request.app.state.review_service.rename_character,
                processing_id,
                str(form.get("current_name", "")),
                str(form.get("new_name", "")),
            )
        except ReviewStateError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _review_redirect(request, processing_id)

    @app.post("/review/{processing_id}/characters/merge")
    async def merge_characters(request: Request, processing_id: str):
        _load_or_404(request, processing_id)
        form = await request.form()
        try:
            await run_in_threadpool(
                request.app.state.review_service.merge_characters,
                processing_id,
                str(form.get("source_name", "")),
                str(form.get("target_name", "")),
                int(str(form.get("known_from", "1"))),
            )
        except (ReviewStateError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _review_redirect(request, processing_id)

    @app.post("/review/{processing_id}/voices")
    async def select_voice(request: Request, processing_id: str):
        _load_or_404(request, processing_id)
        form = await request.form()
        try:
            await run_in_threadpool(
                request.app.state.review_service.select_voice,
                processing_id,
                str(form.get("character", "")),
                str(form.get("voice_id", "")),
            )
        except ReviewStateError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return _review_redirect(request, processing_id)

    @app.post("/review/{processing_id}/preview")
    async def preview_voice(request: Request, processing_id: str) -> FileResponse:
        _load_or_404(request, processing_id)
        form = await request.form()
        try:
            path = await run_in_threadpool(
                request.app.state.review_service.preview_voice,
                processing_id,
                str(form.get("voice_id", "")),
            )
        except (ReviewStateError, SpeechGenerationError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return FileResponse(
            path,
            media_type="audio/wav",
            content_disposition_type="inline",
        )

    @app.post("/review/{processing_id}/generate")
    async def generate_audiobook(
        request: Request,
        processing_id: str,
        background_tasks: BackgroundTasks,
    ):
        _load_or_404(request, processing_id)
        background_tasks.add_task(
            _generate_in_background,
            request.app.state.review_service,
            processing_id,
        )
        return RedirectResponse(
            request.url_for("generation_page", processing_id=processing_id),
            status_code=303,
        )

    @app.get("/generation/{processing_id}", response_class=HTMLResponse)
    async def generation_page(request: Request, processing_id: str):
        project = _load_or_404(request, processing_id)
        if project.output is not None:
            return RedirectResponse(
                request.url_for("result_page", processing_id=processing_id),
                status_code=303,
            )
        return templates.TemplateResponse(
            request=request,
            name="generation.html",
            context={
                "project": project,
                "status": request.app.state.review_service.generation_status(
                    processing_id
                ),
            },
        )

    @app.get("/generation/{processing_id}/status")
    async def generation_status(request: Request, processing_id: str) -> JSONResponse:
        try:
            status = request.app.state.review_service.generation_status(processing_id)
        except ReviewStateError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        project = request.app.state.review_service.load(processing_id)
        status["result_ready"] = project.output is not None
        return JSONResponse(status)

    @app.post("/generation/{processing_id}/cancel")
    async def cancel_generation(request: Request, processing_id: str):
        try:
            request.app.state.review_service.cancel_generation(processing_id)
        except ReviewStateError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return RedirectResponse(
            request.url_for("generation_page", processing_id=processing_id),
            status_code=303,
        )

    @app.get("/results/{processing_id}", response_class=HTMLResponse)
    async def result_page(request: Request, processing_id: str) -> HTMLResponse:
        project = _load_or_404(request, processing_id)
        if project.output is None:
            return _review_redirect(request, processing_id)
        result = ProcessingResult(project.analysis, project.output)
        chapters = [
            {
                "number": chapter.number,
                "title": chapter.title,
                "filename": audio_path.name,
            }
            for chapter, audio_path in zip(
                project.analysis.chapters,
                project.output.chapter_files,
                strict=True,
            )
        ]
        return templates.TemplateResponse(
            request=request,
            name="result.html",
            context={
                "processing_id": processing_id,
                "source_name": project.source_name,
                "result": result,
                "chapters": chapters,
            },
        )

    @app.get("/media/{processing_id}/{filename:path}")
    async def play_audio(
        request: Request,
        processing_id: str,
        filename: str,
    ) -> FileResponse:
        return _serve_result_file(request, processing_id, filename, download=False)

    @app.get("/downloads/{processing_id}/{filename:path}")
    async def download_audio(
        request: Request,
        processing_id: str,
        filename: str,
    ) -> FileResponse:
        return _serve_result_file(request, processing_id, filename, download=True)

    return app


def _generate_in_background(service: ReviewService, processing_id: str) -> None:
    try:
        service.generate(processing_id)
    except Exception as error:
        LOGGER.exception("Background audio generation failed")
        path = service.store.directory(processing_id) / "generation_state.json"
        if not path.is_file():
            import json

            path.write_text(
                json.dumps(
                    {"status": "failed", "error": str(error), "percent": 0},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )


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
    processing_id: str,
    filename: str,
    download: bool,
) -> FileResponse:
    project = _load_or_404(request, processing_id)
    if project.output is None:
        raise HTTPException(status_code=404, detail="Audio no encontrado.")
    allowed_files = {
        path.name: path
        for path in (*project.output.chapter_files, project.output.full_audiobook)
    }
    path = resolve_allowed_output(
        project.output.directory,
        filename,
        allowed_files,
    )
    if path is None:
        raise HTTPException(status_code=404, detail="Audio no encontrado.")
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=path.name,
        content_disposition_type="attachment" if download else "inline",
    )


def _load_or_404(request: Request, processing_id: str) -> ReviewProject:
    try:
        return request.app.state.review_service.load(processing_id)
    except ReviewStateError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _review_response(
    templates: Jinja2Templates,
    request: Request,
    project: ReviewProject,
    page: int = 1,
    speaker: str = "all",
    chapter: int | None = None,
    error: str | None = None,
    status_code: int = 200,
    issue: str = "all",
) -> HTMLResponse:
    dialogues = list(project.dialogues)
    duplicates = project.analysis.registry.duplicate_suggestions()
    duplicate_names = {name for pair in duplicates for name in pair}
    if issue == "unresolved":
        dialogues = [s for s in dialogues if s.speaker == "Unknown"]
    elif issue == "low_confidence":
        threshold = CharacterConfig.from_environment().accept_confidence
        dialogues = [s for s in dialogues if s.confidence is not None and s.confidence < threshold]
    elif issue == "new_characters":
        dialogues = [s for s in dialogues if s.new_character_candidate]
    elif issue == "duplicates":
        dialogues = [s for s in dialogues if s.speaker in duplicate_names]
    elif issue != "all":
        raise HTTPException(status_code=400, detail="Filtro de revisión no válido.")
    dialogues.sort(key=lambda s: (not s.review_needed, s.confidence if s.confidence is not None else -1, s.order))
    if speaker == "unknown":
        dialogues = [item for item in dialogues if item.speaker == "Unknown"]
    elif speaker != "all":
        key = normalize_character_name(speaker)
        dialogues = [
            item
            for item in dialogues
            if normalize_character_name(item.speaker) == key
        ]
    if chapter is not None:
        dialogues = [item for item in dialogues if item.chapter == chapter]
    total_pages = max(1, ceil(len(dialogues) / PAGE_SIZE))
    page = min(page, total_pages)
    start = (page - 1) * PAGE_SIZE
    visible_dialogues = dialogues[start : start + PAGE_SIZE]
    return templates.TemplateResponse(
        request=request,
        name="review.html",
        context={
            "project": project,
            "profiles": project.analysis.registry.profiles,
            "duplicates": duplicates,
            "issue_filter": issue,
            "dialogues": visible_dialogues,
            "filtered_count": len(dialogues),
            "page": page,
            "total_pages": total_pages,
            "speaker_filter": speaker,
            "chapter_filter": chapter,
            "provider_status": request.app.state.review_service.provider_status,
            "error": error,
        },
        status_code=status_code,
    )


def _review_redirect(request: Request, processing_id: str) -> RedirectResponse:
    return RedirectResponse(
        request.url_for("review_page", processing_id=processing_id),
        status_code=303,
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


app = create_app()
