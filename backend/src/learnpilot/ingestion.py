"""Text extraction and chunking. Only the extracted text is kept, never the file."""

import io
import re
from dataclasses import dataclass
from pathlib import PurePath

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, PdfReadError

SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown", ".txt"}
# Chunks are the unit for citations and for picking a concept's sources, so they
# stay at a few paragraphs. A single paragraph longer than this is kept whole.
TARGET_CHUNK_CHARS = 1500
# Below this many non-whitespace characters a document is treated as having no text.
MIN_TEXT_CHARS = 50

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


class IngestionError(Exception):
    """A document that cannot be used; the message is shown to the user."""


@dataclass
class ChunkDraft:
    text: str
    page: int | None = None
    section: str | None = None


def extract_chunks(filename: str, data: bytes) -> list[ChunkDraft]:
    suffix = PurePath(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise IngestionError("Unsupported file type. Upload a PDF, Markdown or text file.")
    if suffix == ".pdf":
        chunks = _pdf_chunks(data)
    elif suffix == ".txt":
        chunks = [ChunkDraft(text) for text in _group_paragraphs(_paragraphs(_decode(data)))]
    else:
        chunks = _markdown_chunks(_decode(data))

    if sum(len(re.sub(r"\s", "", c.text)) for c in chunks) < MIN_TEXT_CHARS:
        if suffix == ".pdf":
            raise IngestionError(
                "This PDF contains no extractable text. It may be a scanned document; "
                "scanned PDFs are not supported."
            )
        raise IngestionError("This document contains no text.")
    return chunks


def title_from_filename(filename: str) -> str:
    return PurePath(filename).stem.strip() or "Untitled"


def _decode(data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    return _clean(text)


def _clean(text: str) -> str:
    # PostgreSQL text columns reject NUL; PDFs sometimes contain it.
    return text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")


def _pdf_chunks(data: bytes) -> list[ChunkDraft]:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")  # many PDFs are "encrypted" with an empty user password
        pages = [_clean(page.extract_text() or "") for page in reader.pages]
    except FileNotDecryptedError as e:
        raise IngestionError("This PDF is password-protected.") from e
    except (PdfReadError, ValueError, KeyError, OSError) as e:
        raise IngestionError("This file is not a readable PDF.") from e

    # Chunks never cross a page boundary, so every chunk has an exact page number.
    return [
        ChunkDraft(text, page=number)
        for number, page_text in enumerate(pages, start=1)
        for text in _group_paragraphs(_paragraphs(page_text))
    ]


def _markdown_chunks(text: str) -> list[ChunkDraft]:
    """Splits at headings; the section is the heading path, e.g. "Basics › Recursion"."""
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    path: list[tuple[int, str]] = []
    in_code = False
    for line in text.split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            in_code = not in_code
        match = None if in_code else HEADING.match(line)
        if match:
            level = len(match.group(1))
            path = [(lvl, title) for lvl, title in path if lvl < level] + [(level, match.group(2))]
            sections.append((" › ".join(title for _, title in path), [line]))
        else:
            sections[-1][1].append(line)

    return [
        ChunkDraft(chunk, section=section)
        for section, lines in sections
        for chunk in _group_paragraphs(_paragraphs("\n".join(lines)))
    ]


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _group_paragraphs(paragraphs: list[str]) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for paragraph in paragraphs:
        if current and size + len(paragraph) > TARGET_CHUNK_CHARS:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(paragraph)
        size += len(paragraph) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks
