"""Bounded extraction and location metadata for the supported document formats."""

import codecs
from dataclasses import dataclass
from typing import BinaryIO
from zipfile import ZipFile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader


MAX_DOCUMENT_BYTES = 30 * 1024 * 1024
MAX_DOCX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_INDEX_CHARACTERS = 1_000_000
MAX_PDF_PAGES = 300
MAX_DOCX_BLOCKS = 5_000


@dataclass
class Content:
    status: str
    chunks: list[tuple[str, dict]]
    truncated: bool = False


def _clean(text: str) -> str:
    return text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")


def _text_chunks(stream: BinaryIO):
    raw = stream.read()
    if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        decoded = raw.decode("utf-32")
    elif raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        decoded = raw.decode("utf-16")
    else:
        try:
            decoded = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            decoded = raw.decode("gb18030")
    if any(ord(character) < 32 and character not in "\n\r\t\f" for character in decoded):
        raise ValueError("Unsupported text encoding.")
    yield decoded, {"kind": "lines", "line": 1}


def _docx_chunks(stream: BinaryIO):
    with ZipFile(stream) as archive:
        members = archive.infolist()
        if len(members) > 10_000 or sum(member.file_size for member in members) > MAX_DOCX_EXPANDED_BYTES:
            raise ValueError("DOCX expanded size exceeds the extraction limit.")
    stream.seek(0)
    document = Document(stream)
    for number, block in enumerate(document.iter_inner_content(), 1):
        if number > MAX_DOCX_BLOCKS:
            yield None, {}
            return
        if isinstance(block, Paragraph):
            yield block.text, {"kind": "paragraph", "block": number}
        elif isinstance(block, Table):
            text = "\n".join("\t".join(cell.text for cell in row.cells) for row in block.rows)
            yield text, {"kind": "table", "block": number}


def _pdf_chunks(pdf: PdfReader):
    for index, page in enumerate(pdf.pages):
        if index >= MAX_PDF_PAGES:
            yield None, {}
            return
        yield page.extract_text() or "", {"kind": "page", "page": index + 1}


def extract(stream: BinaryIO, extension: str, size: int) -> Content:
    parser = {".txt": _text_chunks, ".md": _text_chunks, ".markdown": _text_chunks,
              ".docx": _docx_chunks}.get(extension)
    if parser is None and extension != ".pdf":
        return Content("unsupported", [])
    if size > MAX_DOCUMENT_BYTES:
        return Content("too_large", [])
    try:
        stream.seek(0)
        if extension == ".pdf":
            pdf = PdfReader(stream)
            if pdf.is_encrypted:
                return Content("encrypted", [])
            blocks = _pdf_chunks(pdf)
        else:
            blocks = parser(stream)
        chunks = []
        remaining = MAX_INDEX_CHARACTERS
        truncated = False
        for raw, location in blocks:
            if raw is None:
                truncated = True
                break
            value = _clean(raw)
            if not value.strip():
                continue
            if len(value) > remaining:
                truncated = True
            if remaining:
                chunks.append((value[:remaining], location))
            remaining -= min(remaining, len(value))
            if truncated:
                break
        empty_status = "needs_ocr" if extension == ".pdf" else "no_text"
        return Content("ready" if chunks else empty_status, chunks, truncated)
    except OSError:
        raise
    except Exception:
        # Document parsers raise format-specific exceptions. Index the metadata even when extraction fails.
        return Content("parse_error", [])


CONTENT_MESSAGES = {
    "unsupported": "This file type has no text extractor.",
    "too_large": "This document exceeds the text extraction size limit.",
    "encrypted": "The PDF is encrypted.",
    "needs_ocr": "This PDF has no extractable text. OCR is unavailable in this version.",
    "no_text": "This document contains no extractable text.",
    "parse_error": "The document text could not be extracted.",
}


def make_excerpt(chunks, start: int, limit: int) -> dict:
    text = "\n\n".join(chunk.text for chunk in chunks)
    end = min(start + limit, len(text))
    locations = []
    offset = 0
    for chunk in chunks:
        chunk_end = offset + len(chunk.text)
        if start < chunk_end and end > offset:
            local_start = max(start - offset, 0)
            local_end = min(end - offset, len(chunk.text))
            location = {**chunk.location, "start": offset + local_start, "end": offset + local_end,
                        "local_start": local_start, "local_end": local_end}
            if location["kind"] == "lines":
                location["line"] = chunk.text.count("\n", 0, local_start) + 1
                location["line_end"] = chunk.text.count("\n", 0, max(local_end - 1, local_start)) + 1
            locations.append(location)
        offset = chunk_end + 2
        if offset >= end:
            break

    return {"text": text[start:end], "start": min(start, end), "end": end,
            "total_chars": len(text), "next_start": end if end < len(text) else None,
            "location": locations[0] if locations else None, "locations": locations}
