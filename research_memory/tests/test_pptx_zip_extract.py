from __future__ import annotations

import io
import zipfile
from pathlib import Path

from pptx import Presentation

from research_memory.pipeline.extractors import extract_chunks


def test_pptx_extracts_slide_text(tmp_path: Path) -> None:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "KMX 회의"
    box = slide.shapes.add_textbox(0, 0, 100, 100)
    box.text_frame.paragraphs[0].text = "안건 요약"
    path = tmp_path / "brief.pptx"
    prs.save(path)

    file_type, chunks, err = extract_chunks(path)
    assert err == ""
    assert file_type == "pptx"
    joined = "\n".join(c.text for c in chunks)
    assert "KMX 회의" in joined
    assert "안건 요약" in joined


def test_ppt_ole_gets_placeholder(tmp_path: Path) -> None:
    # Minimal OLE magic so it is not treated as a zip/pptx.
    path = tmp_path / "legacy.ppt"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32)
    file_type, chunks, err = extract_chunks(path)
    assert err == ""
    assert file_type == "ppt"
    assert chunks
    assert "legacy.ppt" in chunks[0].text


def test_zip_lists_members(tmp_path: Path) -> None:
    path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("notes/회의록.txt", "hello")
        zf.writestr("slides/deck.pptx", b"PK\x03\x04fake")

    file_type, chunks, err = extract_chunks(path)
    assert err == ""
    assert file_type == "zip"
    text = chunks[0].text
    assert "bundle.zip" in text
    assert "notes/회의록.txt" in text
    assert "slides/deck.pptx" in text


def test_invalid_zip_raises_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.zip"
    path.write_bytes(b"not a zip")
    file_type, chunks, err = extract_chunks(path)
    assert file_type == "zip"
    assert chunks == []
    assert err
