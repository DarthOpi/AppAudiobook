"""Utilities for reading input text files."""

from pathlib import Path


class TextFileError(ValueError):
    """Raised when an input text file cannot be used."""


def read_text_file(path: Path) -> str:
    """Read and validate a UTF-8 encoded TXT file."""
    if path.suffix.lower() != ".txt":
        raise TextFileError("El archivo de entrada debe tener extensión .txt.")
    if not path.is_file():
        raise TextFileError(f"No se encontró el archivo de entrada: {path}")

    try:
        text = path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError as error:
        raise TextFileError(
            "No se pudo leer el archivo. Guárdalo con codificación UTF-8."
        ) from error
    except OSError as error:
        raise TextFileError(f"No se pudo leer el archivo: {error}") from error

    if not text:
        raise TextFileError("El archivo de entrada está vacío.")

    return text

