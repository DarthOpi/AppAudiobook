"""Create an original, tiny EPUB for local demonstrations; no downloaded books."""

import argparse
from pathlib import Path

from ebooklib import epub


def create_demo_epub(path: Path) -> Path:
    book = epub.EpubBook()
    book.set_identifier("smart-audiobook-demo-09")
    book.set_title("El faro de las dos voces")
    book.set_language("es")
    book.add_author("Smart Audiobook Demo")
    book.add_metadata("DC", "publisher", "Proyecto personal")
    sections = []
    for number, body in enumerate((
        '<h1>Capítulo 1 — La puerta</h1><p><em>La noche parecía interminable.</em></p>'
        '<p>—¿Entramos? —preguntó Elena.</p><p>—Sí —respondió Marcos.</p><hr/><p>Elena abrió la puerta.</p>',
        '<h1>Capítulo 2 — La luz</h1><p>Elena, también conocida como Luz, miró el faro.</p>'
        '<p>—Espera aquí —dijo Elena.</p><p>—De acuerdo —respondió Marcos.</p>',
        '<h1>Capítulo 3 — El regreso</h1><p>Marcos volvió al amanecer.</p>'
        '<p>—Hemos terminado —dijo Marcos.</p><p><strong>La luz seguía encendida.</strong></p>',
    ), start=1):
        chapter = epub.EpubHtml(title=f"Capítulo {number}", file_name=f"chapter_{number}.xhtml", lang="es")
        chapter.content = body
        book.add_item(chapter)
        sections.append(chapter)
    book.toc = tuple(sections)
    book.add_item(epub.EpubNav())
    book.add_item(epub.EpubNcx())
    book.spine = sections
    path.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(path, book, options={"raise_exceptions": True})
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path, nargs="?", default=Path("examples/demo_v09.epub"))
    print(create_demo_epub(parser.parse_args().output))
