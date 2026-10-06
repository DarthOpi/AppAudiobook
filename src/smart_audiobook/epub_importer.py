"""Bounded EPUB adapter: validate the container, then follow its reading spine."""

import hashlib
import re
import stat
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile

from ebooklib import epub
from lxml import html

from smart_audiobook.models import Chapter, Document, DocumentBlock
from smart_audiobook.text_normalizer import normalize_text

MAX_EPUB_TOTAL_BYTES = 200 * 1024 * 1024
MAX_EPUB_MEMBER_BYTES = 8 * 1024 * 1024
MAX_EPUB_MEMBERS = 10_000
SCENE_BREAK = re.compile(r"^(?:\*\s*\*\s*\*|---|§)$")


def safe_member_name(value: str) -> str:
    """Reject filesystem escapes and remote references; never extract members."""
    decoded = unquote(value)
    parsed = urlsplit(decoded)
    path = PurePosixPath(parsed.path)
    if (not decoded or parsed.scheme or parsed.netloc or "\\" in decoded
            or "\x00" in decoded or path.is_absolute() or ".." in path.parts
            or ":" in decoded):
        raise ValueError("EPUB contiene una ruta interna no segura.")
    return path.as_posix()


def validate_epub(path: Path) -> None:
    """Bound expanded bytes and member count before EbookLib reads anything."""
    try:
        with ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_EPUB_MEMBERS:
                raise ValueError("EPUB contiene demasiados archivos.")
            names = set()
            total = 0
            for member in members:
                name = safe_member_name(member.orig_filename)
                if name in names:
                    raise ValueError("EPUB contiene rutas duplicadas.")
                names.add(name)
                total += member.file_size
                if (member.flag_bits & 1 or stat.S_ISLNK(member.external_attr >> 16)
                        or member.file_size > MAX_EPUB_MEMBER_BYTES
                        or total > MAX_EPUB_TOTAL_BYTES
                        or member.file_size > max(1, member.compress_size) * 500):
                    raise ValueError("EPUB protegido o supera los límites de seguridad.")
                if name.lower().endswith((".xml", ".opf", ".ncx", ".xhtml", ".html", ".htm")):
                    data = archive.read(member)
                    if re.search(br"<!\s*(?:ENTITY|DOCTYPE\s+[^>]+(?:SYSTEM|PUBLIC|\[))", data, re.I):
                        raise ValueError("EPUB contiene declaraciones XML externas o entidades.")
                    # Validate references before the library normalizes them.
                    if name.lower().endswith((".xml", ".opf", ".ncx")):
                        for ref in re.findall(br'(?:href|full-path|src)\s*=\s*[\'"]([^\'"]+)', data):
                            safe_member_name(ref.decode("utf-8"))
            if "META-INF/encryption.xml" in names:
                raise ValueError("EPUB con contenido cifrado no soportado.")
            if "META-INF/container.xml" not in names or "mimetype" not in names:
                raise ValueError("El archivo no contiene una estructura EPUB válida.")
            if archive.read("mimetype").strip() != b"application/epub+zip":
                raise ValueError("El archivo no declara el tipo EPUB.")
    except (BadZipFile, KeyError, OSError, UnicodeError) as error:
        raise ValueError("No se pudo leer el contenedor EPUB.") from error


def html_blocks(content: bytes) -> tuple[tuple[DocumentBlock, ...], str, tuple[str, ...]]:
    """Parse offline HTML, keeping paragraph structure and lightweight emphasis."""
    parser = html.HTMLParser(no_network=True, recover=True, encoding="utf-8")
    tree = html.fromstring(content or b"<body></body>", parser=parser)
    raw_text = normalize_text(tree.text_content())
    removed = 0
    for node in list(tree.iter()):
        if not isinstance(node.tag, str):
            continue
        tag = node.tag.rsplit("}", 1)[-1].lower()
        style = re.sub(r"\s+", "", node.get("style", "").lower())
        semantic = node.get("{http://www.idpf.org/2007/ops}type", node.get("epub:type", ""))
        if (tag in {"script", "style", "head", "nav", "button", "form", "iframe", "object"}
                or "hidden" in node.attrib or node.get("aria-hidden") == "true"
                or "display:none" in style or "visibility:hidden" in style
                or semantic in {"pagebreak", "page-list"}):
            if node.getparent() is not None:
                node.drop_tree()
                removed += 1
        elif tag == "br":
            node.tail = "\n" + (node.tail or "")
        elif tag == "hr":
            node.tag, node.text = "p", "***"
    root = tree.find("body") if tree.tag == "html" else tree
    if root is None:
        root = tree
    blocks = []
    block_tags = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "div", "section", "article"}

    def visit(node):
        tag = node.tag.lower() if isinstance(node.tag, str) else ""
        nested = [child for child in node if isinstance(child.tag, str) and child.tag.lower() in block_tags]
        if tag in block_tags and not nested:
            text = normalize_text(node.text_content())
            if text:
                emphasis = tuple(sorted({"italic" if e.tag in {"i", "em"} else "bold"
                    for e in node.iter() if e.tag in {"i", "em", "b", "strong"}}))
                blocks.append(DocumentBlock(text, int(tag[1]) if re.fullmatch(r"h[1-6]", tag) else None,
                                            emphasis, bool(SCENE_BREAK.fullmatch(text))))
            return
        if node.text and node.text.strip():
            blocks.append(DocumentBlock(normalize_text(node.text)))
        for child in node:
            visit(child)
            if child.tail and child.tail.strip():
                blocks.append(DocumentBlock(normalize_text(child.tail)))

    visit(root)
    return tuple(blocks), raw_text, ((f"Se eliminaron {removed} elementos HTML no narrativos u ocultos.",) if removed else ())


def load_epub(path: Path) -> Document:
    validate_epub(path)
    # EbookLib reads a bounded archive. No extraction or network fetch occurs.
    book = epub.read_epub(path, options={"ignore_ncx": True})
    def metadata(name):
        values = book.get_metadata("DC", name)
        return normalize_text(str(values[0][0])) or None if values else None

    titles = {}
    def collect_toc(items):
        for item in items:
            if isinstance(item, tuple):
                collect_toc([item[0]])
                collect_toc(item[1])
            elif getattr(item, "href", None):
                titles[item.href.split("#", 1)[0]] = str(item.title)
    collect_toc(book.toc)
    ordered = [(book.get_item_with_id(item_id), linear) for item_id, linear in book.spine]
    if not ordered:
        ordered = [(item, "yes") for item in book.get_items()
                   if isinstance(item, epub.EpubHtml) and not isinstance(item, epub.EpubNav)]
    chapters = []
    for item, linear in ordered:
        if not isinstance(item, epub.EpubHtml):
            continue
        reference = safe_member_name(item.file_name)
        blocks, raw, notes = html_blocks(item.content)
        navigation = isinstance(item, epub.EpubNav)
        if not blocks and not navigation:
            continue
        title = titles.get(reference) or next((b.text for b in blocks if b.heading_level), None) or item.title or f"Section {len(chapters)+1}"
        kind = "table_of_contents" if navigation else _section_kind(title, reference, item.properties)
        headings = [i for i, b in enumerate(blocks) if b.heading_level in {1, 2}]
        # An EPUB item is already a section; split only when it contains peers.
        if len(headings) > 1:
            level = min(blocks[i].heading_level for i in headings)
            headings = [i for i in headings if blocks[i].heading_level == level]
        if not headings:
            from smart_audiobook.chapter_detection import is_chapter_heading
            headings = [i for i, block in enumerate(blocks) if is_chapter_heading(block.text)]
        starts = (([0] if headings[0] != 0 else []) + headings) if len(headings) > 1 else [0]
        groups = [(start, starts[n+1] if n+1 < len(starts) else len(blocks)) for n, start in enumerate(starts)]
        for part, (start, end) in enumerate(groups):
            section = blocks[start:end]
            section_title = (section[0].text if section and (section[0].heading_level or start in headings)
                             else title) if len(groups) > 1 else title
            text = normalize_text("\n\n".join(b.text for b in section))
            if not text and navigation:
                text = "Table of Contents"
            identity = hashlib.sha256(f"{reference}#{part}".encode()).hexdigest()[:16]
            chapters.append(Chapter(len(chapters)+1, section_title, text, f"ch_{identity}",
                f"{reference}#section-{part+1}", raw if len(groups) == 1 else text, section,
                selected_for_processing=not navigation and linear != "no", narrate=not navigation,
                section_kind=kind, word_count=len(text.split()), cleaning_notes=notes))
    if not chapters:
        raise ValueError("EPUB no contiene secciones de texto accesibles.")
    return Document(metadata("title") or path.stem, path.resolve(), "epub", "", tuple(chapters),
                    author=metadata("creator"), language=metadata("language"),
                    publisher=metadata("publisher"), identifier=metadata("identifier"))


def _section_kind(title, reference, properties):
    value = f"{title} {reference} {' '.join(properties)}".casefold()
    for kind, words in {
        "copyright": ("copyright", "derechos reservados"), "cover": ("cover", "portada"),
        "title_page": ("titlepage", "title-page"), "dedication": ("dedication", "dedicatoria"),
        "acknowledgements": ("acknowledg", "agradecimientos"), "appendix": ("appendix", "apéndice"),
        "table_of_contents": ("table of contents", "contents.xhtml", "índice"),
    }.items():
        if any(word in value for word in words):
            return kind
    return None
