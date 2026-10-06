"""Upload and path validation for the local web interface."""

from pathlib import Path
from zipfile import BadZipFile, ZipFile, is_zipfile

from smart_audiobook.output_files import safe_filename

ALLOWED_EXTENSIONS = frozenset({".txt", ".pdf", ".docx", ".epub"})
MAX_UPLOAD_SIZE = 20 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED_SIZE = 100 * 1024 * 1024


class UploadValidationError(ValueError):
    """An expected, user-facing upload validation error."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def sanitize_upload_name(original_name: str) -> str:
    """Discard directories and return a controlled portable filename."""
    leaf_name = original_name.replace("\\", "/").rsplit("/", 1)[-1]
    source = Path(leaf_name)
    extension = source.suffix.casefold()
    if extension not in ALLOWED_EXTENSIONS:
        raise UploadValidationError(
            "Formato no soportado. Utiliza un archivo TXT, PDF o DOCX, o EPUB."
        )
    return f"{safe_filename(source.stem, 'document')}{extension}"


def validate_uploaded_content(path: Path) -> None:
    """Validate basic file signatures instead of trusting the extension alone."""
    extension = path.suffix.casefold()
    if path.stat().st_size == 0:
        raise UploadValidationError("El archivo está vacío.")

    if extension == ".txt":
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise UploadValidationError(
                "El TXT debe utilizar codificación UTF-8."
            ) from error
        if not text.strip():
            raise UploadValidationError("El archivo está vacío.")
        if "\x00" in text:
            raise UploadValidationError("El archivo no parece ser un TXT válido.")
        return

    if extension == ".pdf":
        if not path.read_bytes()[:8].startswith(b"%PDF-"):
            raise UploadValidationError("El archivo no parece ser un PDF válido.")
        return

    if extension == ".docx":
        _validate_docx_package(path)
        return

    if extension == ".epub":
        from smart_audiobook.epub_importer import validate_epub
        try:
            validate_epub(path)
        except ValueError as error:
            raise UploadValidationError(str(error)) from error
        return

    raise UploadValidationError("Formato no soportado.")


def resolve_allowed_output(
    output_directory: Path,
    filename: str,
    allowed_files: dict[str, Path],
) -> Path | None:
    """Resolve only an explicitly registered file inside one result directory."""
    candidate = allowed_files.get(filename)
    if candidate is None:
        return None
    resolved_directory = output_directory.resolve()
    resolved_candidate = candidate.resolve()
    if not resolved_candidate.is_relative_to(resolved_directory):
        return None
    if not resolved_candidate.is_file():
        return None
    return resolved_candidate


def _validate_docx_package(path: Path) -> None:
    if not is_zipfile(path):
        raise UploadValidationError("El archivo no parece ser un DOCX válido.")
    try:
        with ZipFile(path) as archive:
            members = archive.infolist()
            names = {member.filename for member in members}
            required = {"[Content_Types].xml", "word/document.xml"}
            if not required.issubset(names):
                raise UploadValidationError(
                    "El archivo no parece ser un DOCX válido."
                )
            if sum(member.file_size for member in members) > MAX_DOCX_UNCOMPRESSED_SIZE:
                raise UploadValidationError(
                    "El contenido descomprimido del DOCX es demasiado grande.",
                    status_code=413,
                )
    except BadZipFile as error:
        raise UploadValidationError(
            "El archivo no parece ser un DOCX válido."
        ) from error
