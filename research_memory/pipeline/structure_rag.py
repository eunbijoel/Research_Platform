"""Structure-aware block extraction + chunking for RAG A/B experiments.

Reuses docsim DOCX helpers (_iter_block_items, _table_text, page breaks) and
docsim PDF page text. Does not modify production ingest indexes.
"""

from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from research_memory.config import CHUNK_OVERLAP, CHUNK_SIZE
from research_memory.pipeline.chunking import refine_chunks
from research_memory.pipeline.extractors import extract_chunks
from research_memory.schema import TextChunk

ChunkStrategy = Literal["legacy", "structure"]

_HEADING_STYLE_RE = re.compile(r"^(Heading|제목|heading)\s*(\d+)?", re.IGNORECASE)
_PDF_HEADING_RE = re.compile(
    r"^(\d+(\.\d+){0,4}|[IVXLC]+\.?|[가-힣]\.|제\s*\d+\s*[장절항조])\s+\S+"
)


@dataclass
class StructureBlock:
    text: str
    block_type: str  # heading | paragraph | table | page
    page: int | None = None
    heading_path: str = ""
    location: str = ""


@dataclass
class StructureChunk:
    text: str
    location: str
    chunk_index: int = 0
    page: int | None = None
    heading_path: str = ""
    block_type: str = "paragraph"
    chunk_strategy: str = "structure"
    document_id: str = ""
    filename: str = ""

    def to_index_row(self) -> dict[str, Any]:
        return {
            "chunk_id": f"{self.document_id}:{self.chunk_index}",
            "document_id": self.document_id,
            "filename": self.filename,
            "chunk_index": self.chunk_index,
            "location": self.location,
            "page": self.page,
            "text": self.text,
            "heading_path": self.heading_path,
            "block_type": self.block_type,
            "chunk_strategy": self.chunk_strategy,
            "title": "",
            "project_id": "",
            "doc_type": "",
            "status": "ready",
            "document_role": "project_document",
            "metadata_json": "",
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_structure_blocks(path: Path) -> list[StructureBlock]:
    """DOCX/PDF only. Raises ValueError for unsupported types."""
    ext = path.suffix.lower()
    if ext == ".docx":
        return _blocks_docx(path)
    if ext == ".pdf":
        return _blocks_pdf(path)
    raise ValueError(f"structure extraction supports .docx/.pdf only, got {ext}")


def build_chunks_for_strategy(
    path: Path,
    *,
    strategy: ChunkStrategy,
    document_id: str,
    filename: str | None = None,
) -> list[StructureChunk]:
    name = filename or path.name
    if strategy == "legacy":
        return _legacy_chunks(path, document_id=document_id, filename=name)
    return _structure_chunks(path, document_id=document_id, filename=name)


def _legacy_chunks(path: Path, *, document_id: str, filename: str) -> list[StructureChunk]:
    _ftype, raw, err = extract_chunks(path)
    if err:
        raise ValueError(err)
    refined = refine_chunks(raw)
    out: list[StructureChunk] = []
    for ch in refined:
        out.append(
            StructureChunk(
                text=ch.text,
                location=ch.location,
                chunk_index=ch.chunk_index,
                page=ch.page,
                heading_path="",
                block_type="page" if ch.page is not None else "paragraph",
                chunk_strategy="legacy",
                document_id=document_id,
                filename=filename,
            )
        )
    return out


def _structure_chunks(path: Path, *, document_id: str, filename: str) -> list[StructureChunk]:
    blocks = extract_structure_blocks(path)
    sections = _group_sections(blocks)
    out: list[StructureChunk] = []
    idx = 0
    for section in sections:
        text = section["text"].strip()
        if not text:
            continue
        heading_path = section["heading_path"]
        page = section["page"]
        block_type = section["block_type"]
        base_loc = heading_path or section["location"] or "본문"
        windows = _window_text(text)
        for part_i, window in enumerate(windows, start=1):
            loc = base_loc if part_i == 1 else f"{base_loc} · part {part_i}"
            out.append(
                StructureChunk(
                    text=window,
                    location=loc,
                    chunk_index=idx,
                    page=page,
                    heading_path=heading_path,
                    block_type=block_type,
                    chunk_strategy="structure",
                    document_id=document_id,
                    filename=filename,
                )
            )
            idx += 1
    return out


def _window_text(text: str) -> list[str]:
    if len(text) <= CHUNK_SIZE:
        return [text]
    windows: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_SIZE)
        window = text[start:end].strip()
        if window:
            windows.append(window)
        if end >= len(text):
            break
        start = max(0, end - CHUNK_OVERLAP)
    return windows


def _group_sections(blocks: list[StructureBlock]) -> list[dict[str, Any]]:
    """Merge body under heading path; flush on heading, page change, or table."""
    sections: list[dict[str, Any]] = []
    path_stack: list[str] = []
    buf: list[str] = []
    buf_type = "paragraph"
    buf_page: int | None = None
    buf_loc = ""

    def flush() -> None:
        nonlocal buf, buf_type, buf_page, buf_loc
        text = "\n".join(buf).strip()
        if text:
            heading_path = " > ".join(path_stack)
            sections.append(
                {
                    "text": text,
                    "heading_path": heading_path,
                    "block_type": buf_type,
                    "page": buf_page,
                    "location": heading_path or buf_loc or "본문",
                }
            )
        buf = []
        buf_type = "paragraph"
        buf_page = None
        buf_loc = ""

    for block in blocks:
        if block.block_type == "heading":
            flush()
            title = block.text.strip()
            level = _heading_level_from_location(block.location)
            while len(path_stack) >= level:
                path_stack.pop()
            path_stack.append(title)
            continue
        if block.block_type == "table":
            flush()
            heading_path = " > ".join(path_stack)
            sections.append(
                {
                    "text": block.text.strip(),
                    "heading_path": heading_path,
                    "block_type": "table",
                    "page": block.page,
                    "location": heading_path or block.location or "표",
                }
            )
            continue
        # Keep page-scoped sections so PDF windows stay on the right page.
        if buf and block.page is not None and buf_page is not None and block.page != buf_page:
            flush()
        if not buf:
            buf_type = block.block_type
            buf_page = block.page
            buf_loc = block.location
        if block.page is not None and buf_page is None:
            buf_page = block.page
        buf.append(block.text.strip())
    flush()
    return sections


def _heading_level_from_location(location: str) -> int:
    m = re.search(r"(\d+)", location or "")
    if m:
        return max(1, min(6, int(m.group(1))))
    return 1


def _blocks_docx(path: Path) -> list[StructureBlock]:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    from research_memory.engine.docsim.parsers.docx_parser import (
        _iter_block_items,
        _paragraph_has_page_break,
        _table_text,
    )

    document = Document(io.BytesIO(path.read_bytes()))
    blocks: list[StructureBlock] = []
    page = 1
    para_n = 0
    table_n = 0
    for block in _iter_block_items(document):
        if isinstance(block, Paragraph):
            text = (block.text or "").strip()
            if text:
                style_name = ""
                try:
                    style_name = (block.style.name or "") if block.style else ""
                except Exception:  # noqa: BLE001
                    style_name = ""
                if _is_heading_style(style_name) or _looks_like_text_heading(text):
                    level = (
                        _heading_level_from_style(style_name)
                        if _is_heading_style(style_name)
                        else 1
                    )
                    blocks.append(
                        StructureBlock(
                            text=text,
                            block_type="heading",
                            page=page,
                            location=f"heading {level}",
                        )
                    )
                else:
                    para_n += 1
                    blocks.append(
                        StructureBlock(
                            text=text,
                            block_type="paragraph",
                            page=page,
                            location=f"문단 {para_n}",
                        )
                    )
            if _paragraph_has_page_break(block):
                page += 1
        elif isinstance(block, Table):
            text = _table_text(block)
            if text:
                table_n += 1
                blocks.append(
                    StructureBlock(
                        text=text,
                        block_type="table",
                        page=page,
                        location=f"표 {table_n}",
                    )
                )
    return blocks


def _is_heading_style(style_name: str) -> bool:
    return bool(_HEADING_STYLE_RE.match((style_name or "").strip()))


def _heading_level_from_style(style_name: str) -> int:
    m = _HEADING_STYLE_RE.match((style_name or "").strip())
    if m and m.group(2):
        return max(1, min(6, int(m.group(2))))
    return 1


def _blocks_pdf(path: Path) -> list[StructureBlock]:
    """Reuse docsim PDF page text; add light heading heuristics inside each page."""
    from research_memory.engine.docsim.parsers.pdf_parser import parse_pdf

    result = parse_pdf(path.name, path.read_bytes())
    if not result.success:
        raise ValueError(result.error_message or "PDF parse failed")
    blocks: list[StructureBlock] = []
    for page in result.pages:
        page_no = int(page.page_number)
        lines = [ln.strip() for ln in (page.text or "").splitlines() if ln.strip()]
        if not lines:
            continue
        body: list[str] = []
        for line in lines:
            if _looks_like_pdf_heading(line):
                if body:
                    blocks.append(
                        StructureBlock(
                            text="\n".join(body),
                            block_type="paragraph",
                            page=page_no,
                            location=f"{page_no}페이지",
                        )
                    )
                    body = []
                blocks.append(
                    StructureBlock(
                        text=line,
                        block_type="heading",
                        page=page_no,
                        location="heading 1",
                    )
                )
            else:
                body.append(line)
        if body:
            blocks.append(
                StructureBlock(
                    text="\n".join(body),
                    block_type="page",
                    page=page_no,
                    location=f"{page_no}페이지",
                )
            )
    return blocks


def _looks_like_pdf_heading(line: str) -> bool:
    return _looks_like_text_heading(line)


def _looks_like_text_heading(line: str) -> bool:
    text = (line or "").strip()
    if not text or len(text) > 60:
        return False
    if _PDF_HEADING_RE.match(text):
        return True
    # Short label-like lines (KPI docs often lack Word Heading styles).
    if len(text) <= 40 and not text.endswith((".", ",", "다", "요", "음", "함", "음.", "다.")):
        if re.match(r"^[\d\.]+\s*\S+", text):
            return True
        if re.match(r"^[\[\(【].+[\]\)】]$", text):
            return True
        if "정확도" in text and len(text) <= 30:
            return True
        if text.endswith(("기준", "방법", "정의", "개요", "목적", "범위", "산출식")):
            return True
    return False
