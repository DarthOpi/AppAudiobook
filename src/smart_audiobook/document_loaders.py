"""Document loaders for the supported input formats."""

import re
from abc import ABC, abstractmethod
from collections import Counter
from pathlib import Path
from zipfile import BadZipFile

from smart_audiobook.chapter_detection import detect_chapters
from smart_audiobook.models import Document, DocumentBlock, DocumentFormat
from smart_audiobook.text_normalizer import normalize_text


class DocumentLoadError(ValueError):
    """Raised when a source document cannot be loaded safely."""


class DocumentLoader(ABC):
    """Shared interface implemented by every source-format adapter."""

    @abstractmethod
    def load(self, path: Path) -> Document:
        """Extract and normalize one source document."""


class TxtDocumentLoader(DocumentLoader):
    def load(self, path: Path) -> Document:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise DocumentLoadError(
                "No se pudo leer el TXT. Guárdalo con codificación UTF-8."
            ) from error
        except OSError as error:
            raise DocumentLoadError(f"No se pudo leer el TXT: {error}") from error
        return _build_document(path, "txt", text)


class PdfDocumentLoader(DocumentLoader):
    def load(self, path: Path) -> Document:
        try:
            from pypdf import PdfReader

            reader = PdfReader(path)
            if reader.is_encrypted:
                try:
                    unlocked = reader.decrypt("")
                except Exception as error:
                    raise DocumentLoadError(
                        "El PDF está protegido y no se puede leer sin contraseña."
                    ) from error
                if not unlocked:
                    raise DocumentLoadError(
                        "El PDF está protegido y no se puede leer sin contraseña."
                    )
            pages = [page.extract_text() or "" for page in reader.pages]
            metadata_title = (
                (getattr(reader.metadata, "title", None) or "").strip()
                if reader.metadata
                else ""
            )
        except DocumentLoadError:
            raise
        except Exception as error:
            raise DocumentLoadError(f"No se pudo leer el PDF: {error}") from error

        if not any(page.strip() for page in pages):
            raise DocumentLoadError(
                "El PDF no contiene texto extraíble. Puede ser un documento "
                "escaneado y necesitar OCR, que todavía no forma parte de V0.4."
            )
        return _build_document(
            path,
            "pdf",
            _clean_pdf_pages(pages),
            title=metadata_title,
        )


class DocxDocumentLoader(DocumentLoader):
    def load(self, path: Path) -> Document:
        try:
            from docx import Document as WordDocument
            from docx.opc.exceptions import PackageNotFoundError

            word_document = WordDocument(path)
            blocks: list[DocumentBlock] = []
            title = (word_document.core_properties.title or "").strip()
            for paragraph in word_document.paragraphs:
                text = normalize_text(paragraph.text)
                if not text:
                    continue
                style_name = paragraph.style.name if paragraph.style else ""
                if style_name.casefold() == "title":
                    if not title:
                        title = text
                    continue
                blocks.append(
                    DocumentBlock(
                        text=text,
                        heading_level=_heading_level(style_name),
                    )
                )
        except (PackageNotFoundError, BadZipFile, OSError, ValueError) as error:
            raise DocumentLoadError(f"No se pudo leer el DOCX: {error}") from error
        except Exception as error:
            raise DocumentLoadError(f"No se pudo procesar el DOCX: {error}") from error

        full_text = normalize_text("\n\n".join(block.text for block in blocks))
        if not full_text:
            raise DocumentLoadError("El documento DOCX está vacío.")
        document = Document(
            title=title or path.stem,
            source_path=path.resolve(),
            format="docx",
            full_text=full_text,
            blocks=tuple(blocks),
        )
        return detect_chapters(document)


_LOADERS: dict[str, DocumentLoader] = {
    ".txt": TxtDocumentLoader(),
    ".pdf": PdfDocumentLoader(),
    ".docx": DocxDocumentLoader(),
}


def load_document(path: Path) -> Document:
    """Load a document by extension through the common adapter interface."""
    if not path.is_file():
        raise DocumentLoadError(f"No se encontró el archivo de entrada: {path}")
    loader = _LOADERS.get(path.suffix.casefold())
    if loader is None:
        supported = ", ".join(sorted(_LOADERS))
        raise DocumentLoadError(
            f"Formato no soportado: {path.suffix or '(sin extensión)'}. "
            f"Formatos disponibles: {supported}."
        )
    return loader.load(path)


def _build_document(
    path: Path,
    document_format: DocumentFormat,
    text: str,
    title: str = "",
) -> Document:
    normalized = normalize_text(text)
    if not normalized:
        raise DocumentLoadError(f"El documento {document_format.upper()} está vacío.")
    blocks = tuple(
        DocumentBlock(text=line.strip())
        for line in normalized.splitlines()
        if line.strip()
    )
    return detect_chapters(
        Document(
            title=normalize_text(title) or path.stem,
            source_path=path.resolve(),
            format=document_format,
            full_text=normalized,
            blocks=blocks,
        )
    )


def _heading_level(style_name: str) -> int | None:
    match = re.fullmatch(r"(?:heading|t[ií]tulo)\s*([1-9])", style_name, re.I)
    return int(match.group(1)) if match else None


def _clean_pdf_pages(pages: list[str]) -> str:
    """Remove obvious page numbers and repeated first/last lines."""
    page_lines = [
        [line.strip() for line in page.splitlines() if line.strip()]
        for page in pages
    ]
    boundary_lines = [
        line
        for lines in page_lines
        for line in (lines[:1] + lines[-1:])
        if line
    ]
    counts = Counter(boundary_lines)
    repetition_threshold = max(2, (len(page_lines) + 1) // 2)
    repeated = {
        line for line, count in counts.items() if count >= repetition_threshold
    }
    cleaned_pages: list[str] = []
    for lines in page_lines:
        useful_lines = [
            line
            for line in lines
            if line not in repeated
            and not re.fullmatch(r"(?:p[aá]gina\s+)?\d+", line, re.I)
        ]
        cleaned_pages.append("\n".join(useful_lines))
    extracted = "\n\n".join(cleaned_pages)
    extracted = re.sub(
        r"(?<=[a-záéíóúüñ])-\n(?=[a-záéíóúüñ])",
        "",
        extracted,
        flags=re.IGNORECASE,
    )
    return normalize_text(extracted)
