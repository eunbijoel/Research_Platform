from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pandas as pd
from docx import Document
from pypdf import PdfReader

from research_memory.schema import TextChunk


def extract_chunks(path: Path) -> tuple[str, list[TextChunk], str]:
    """
    Return (file_type, chunks, error).
    error is empty on success; chunks may still be empty for blank docs.
    """
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            return "pdf", _pdf(path), ""
        if ext in {".docx", ".docm"}:
            return "docx", _docx(path), ""
        if ext == ".doc":
            return "doc", _doc(path), ""
        if ext == ".pptx":
            return "pptx", _pptx(path), ""
        if ext == ".ppt":
            return "ppt", _ppt(path), ""
        if ext in {".txt", ".md"}:
            return ext.lstrip("."), _plain(path), ""
        if ext == ".csv":
            return "csv", _csv(path), ""
        if ext in {".xlsx", ".xls"}:
            return "excel", _excel(path), ""
        if ext == ".hwpx":
            return "hwpx", _hwpx(path), ""
        if ext == ".hwp":
            return "hwp", _hwp(path), ""
        if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            return "image", _image(path), ""
        if ext == ".zip":
            return "zip", _zip(path), ""
        return "unknown", [], f"Unsupported extension: {ext}"
    except Exception as exc:  # noqa: BLE001 — surface parse failures to ingest
        return ext.lstrip(".") or "unknown", [], str(exc)


def extract_chunks_from_bytes(data: bytes, filename: str) -> tuple[str, list[TextChunk], str]:
    """Extract chunks from uploaded file bytes using extension inferred from filename."""
    suffix = Path(filename).suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        temp_path = Path(tmp.name)
    try:
        return extract_chunks(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)


def _pdf(path: Path) -> list[TextChunk]:
    reader = PdfReader(str(path))
    chunks: list[TextChunk] = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            chunks.append(TextChunk(text=text, location=f"{i}페이지", page=i))
    return chunks


def _docx(path: Path) -> list[TextChunk]:
    doc = Document(str(path))
    chunks: list[TextChunk] = []
    para_n = 0
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        para_n += 1
        chunks.append(TextChunk(text=text, location=f"문단 {para_n}"))
    for t_i, table in enumerate(doc.tables, start=1):
        for row in table.rows:
            row_text = " | ".join(c.text.strip() for c in row.cells if c.text.strip())
            if row_text:
                chunks.append(TextChunk(text=row_text, location=f"표 {t_i}"))
    if chunks:
        return chunks
    # Still index the file so Home upload does not fail on near-empty Word docs.
    return [TextChunk(text=f"[Word 문서] {path.name}", location="파일명")]


def _doc(path: Path) -> list[TextChunk]:
    """Legacy Word .doc (OLE) or mislabeled .docx saved with a .doc extension."""
    head = path.read_bytes()[:8]
    if head[:2] == b"PK":
        return _docx(path)
    try:
        import olefile
    except ImportError as exc:  # pragma: no cover
        raise ValueError("olefile 패키지가 필요합니다 (.doc 지원).") from exc
    if not olefile.isOleFile(str(path)):
        raise ValueError(
            "유효한 .doc(워드 97-2003) 파일이 아닙니다. "
            "Word에서 .docx로 저장한 뒤 다시 올려 주세요."
        )
    ole = olefile.OleFileIO(str(path))
    try:
        raw = b""
        for entry in ole.listdir():
            if entry and str(entry[-1]).lower() == "worddocument":
                raw = ole.openstream(entry).read()
                break
        if not raw:
            raise ValueError("WordDocument 스트림을 찾지 못했습니다.")
        text = _ole_word_text(raw)
        if not text.strip():
            raise ValueError(
                "구형 .doc에서 텍스트를 추출하지 못했습니다. "
                "Word에서 .docx로 저장한 뒤 다시 올려 주세요."
            )
        return [TextChunk(text=text.strip(), location="전체")]
    finally:
        ole.close()


def _ole_word_text(raw: bytes) -> str:
    """Best-effort readable text from a WordDocument OLE stream (no antiword)."""
    pattern = re.compile(
        r"[\uac00-\ud7a3A-Za-z0-9]"
        r"[\uac00-\ud7a3A-Za-z0-9\s\.,,，、。:：;；\-_()（）\[\]/·]{5,}"
    )
    found: list[str] = []
    for align in (0, 1):
        data = raw[align:]
        if len(data) % 2:
            data = data[:-1]
        decoded = data.decode("utf-16le", errors="ignore")
        for match in pattern.findall(decoded):
            piece = " ".join(match.split())
            if len(piece) >= 6:
                found.append(piece)
    if not found:
        ascii_parts = re.findall(rb"[ -~]{8,}", raw)
        found = [p.decode("ascii", errors="ignore").strip() for p in ascii_parts]
    # Preserve order, drop near-duplicates
    out: list[str] = []
    seen: set[str] = set()
    for piece in found:
        key = piece[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(piece)
        if len(out) >= 200:
            break
    return "\n".join(out)


def _plain(path: Path) -> list[TextChunk]:
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return []
    return [TextChunk(text=text, location="전체")]


def _csv(path: Path) -> list[TextChunk]:
    df = pd.read_csv(path)
    preview = df.head(40).to_csv(index=False)
    summary = f"CSV columns: {', '.join(map(str, df.columns))} | rows={len(df)}\n{preview}"
    return [TextChunk(text=summary, location="시트 preview")]


def _excel(path: Path) -> list[TextChunk]:
    book = pd.read_excel(path, sheet_name=None)
    chunks: list[TextChunk] = []
    for name, df in book.items():
        preview = df.head(30).to_csv(index=False)
        text = f"Sheet={name} columns={list(df.columns)} rows={len(df)}\n{preview}"
        chunks.append(TextChunk(text=text, location=f"시트 {name}"))
    return chunks


def _hwp(path: Path) -> list[TextChunk]:
    """HWP 5.x (OLE) text extraction; mislabeled HWPX is handled by the parser."""
    from research_memory.engine.docsim.parsers.hwp_parser import parse_hwp

    data = path.read_bytes()
    result = parse_hwp(path.name, data)
    if not result.success:
        raise ValueError(result.error_message or "HWP 텍스트 추출 실패")
    chunks: list[TextChunk] = []
    for page in result.pages:
        text = (page.text or "").strip()
        if not text:
            continue
        chunks.append(
            TextChunk(
                text=text,
                location=page.location or f"섹션 {page.page_number}",
                page=page.page_number,
            )
        )
    if chunks:
        return chunks
    combined = "\n".join(
        (s.normalized_text or s.text or "").strip()
        for s in result.sentences
        if (s.normalized_text or s.text or "").strip()
    ).strip()
    if combined:
        return [TextChunk(text=combined, location="전체")]
    return []


def _image(path: Path) -> list[TextChunk]:
    """Indexable placeholder for image attachments (no OCR)."""
    name = path.name
    fmt = path.suffix.lstrip(".").upper() or "IMAGE"
    detail = f"[이미지] {name}\n형식={fmt}"
    try:
        from PIL import Image

        with Image.open(path) as im:
            w, h = im.size
            detail = f"[이미지] {name}\n형식={fmt} 크기={w}×{h} 모드={im.mode}"
    except Exception:
        pass
    return [TextChunk(text=detail, location="이미지")]


def _pptx(path: Path) -> list[TextChunk]:
    """Slide text (+ notes) via python-pptx. Empty decks still get a filename chunk."""
    from pptx import Presentation

    prs = Presentation(str(path))
    chunks: list[TextChunk] = []
    for idx, slide in enumerate(prs.slides, start=1):
        parts: list[str] = []
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            for para in shape.text_frame.paragraphs:
                line = (para.text or "").strip()
                if line:
                    parts.append(line)
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame
            note_text = (notes.text or "").strip() if notes else ""
            if note_text:
                parts.append(note_text)
        text = "\n".join(parts).strip()
        if text:
            chunks.append(TextChunk(text=text, location=f"슬라이드 {idx}", page=idx))
    if chunks:
        return chunks
    return [TextChunk(text=f"[PowerPoint] {path.name}", location="파일명")]


def _ppt(path: Path) -> list[TextChunk]:
    """Legacy .ppt: mislabeled .pptx opens as pptx; OLE .ppt gets a searchable stub."""
    head = path.read_bytes()[:8]
    if head[:2] == b"PK":
        return _pptx(path)
    return [
        TextChunk(
            text=(
                f"[PowerPoint] {path.name}\n"
                "구형 .ppt(바이너리)는 텍스트 추출을 지원하지 않습니다. "
                "PowerPoint에서 .pptx로 저장하면 본문을 검색할 수 있습니다."
            ),
            location="파일명",
        )
    ]


def _zip(path: Path) -> list[TextChunk]:
    """Index ZIP member names so calendar/home attach succeeds (no nested unpack)."""
    if not zipfile.is_zipfile(path):
        raise ValueError("유효한 .zip 파일이 아닙니다.")
    lines = [f"[ZIP] {path.name}"]
    with zipfile.ZipFile(path) as zf:
        names = [info.filename for info in zf.infolist() if not info.is_dir()]
        lines.append(f"파일 {len(names)}개")
        for name in names[:80]:
            lines.append(f"· {name}")
        if len(names) > 80:
            lines.append(f"… 외 {len(names) - 80}개")
    return [TextChunk(text="\n".join(lines), location="목록")]


def _hwpx(path: Path) -> list[TextChunk]:
    """Best-effort HWPX (zip+xml) text extraction without hwpilot."""
    head = path.read_bytes()[:8]
    # Classic .hwp is OLE compound; real .hwpx is a ZIP package.
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise ValueError(
            "확장자는 .hwpx지만 실제 파일은 구형 .hwp(한글 바이너리)입니다. "
            "한글에서 [다른 이름으로 저장] → .hwpx 또는 PDF/DOCX로 변환 후 다시 올려 주세요."
        )
    if head[:2] != b"PK":
        raise ValueError(
            "유효한 .hwpx(ZIP) 파일이 아닙니다. "
            "파일이 손상되었거나 확장자만 .hwpx인 다른 형식일 수 있습니다."
        )
    texts: list[str] = []
    with zipfile.ZipFile(path) as zf:
        section_names = sorted(
            n for n in zf.namelist() if re.search(r"Contents/section\d+\.xml$", n)
        )
        if not section_names:
            section_names = [n for n in zf.namelist() if n.endswith(".xml")]
        for name in section_names:
            raw = zf.read(name)
            root = ET.fromstring(raw)
            parts = [
                (node.text or "").strip()
                for node in root.iter()
                if node.text and node.text.strip()
            ]
            joined = "\n".join(parts).strip()
            if joined:
                texts.append(joined)
    chunks = [
        TextChunk(text=t, location=f"section {i}")
        for i, t in enumerate(texts, start=1)
    ]
    return chunks
