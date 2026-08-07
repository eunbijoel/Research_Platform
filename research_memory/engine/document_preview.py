from __future__ import annotations

import base64
import html
from pathlib import Path
from typing import Any

from research_memory.config import RAW_DIR


def resolve_document_file(doc: dict[str, Any] | None) -> Path | None:
    """Resolve original uploaded file path, with RAW_DIR filename fallback."""
    if not doc:
        return None
    candidates: list[Path] = []
    stored = str(doc.get("stored_path") or "").strip()
    filename = str(doc.get("filename") or "").strip()
    if stored:
        candidates.append(Path(stored))
        # Recover truncated/odd absolute paths that still end with data/raw/<name>
        norm = stored.replace("\\", "/")
        if "/data/raw/" in norm:
            candidates.append(RAW_DIR / Path(norm).name)
        elif "data/raw/" in norm:
            candidates.append(RAW_DIR / Path(norm).name)
    if filename:
        candidates.append(RAW_DIR / filename)
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def preview_kind(path: Path | None, *, file_type: str = "", filename: str = "") -> str:
    ext = ""
    if path is not None:
        ext = path.suffix.lower()
    if not ext:
        name = (filename or "").lower()
        ext = Path(name).suffix.lower()
    if not ext and file_type:
        ft = file_type.lower().strip(".")
        ext = f".{ft}" if ft else ""
    if ext == ".pdf":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext in {".txt", ".md", ".markdown"}:
        return "text"
    if ext in {".csv", ".xlsx", ".xls"}:
        return "table"
    if ext == ".hwpx":
        return "hwpx"
    return "other"


def docx_to_html(path: Path) -> tuple[str, str]:
    """Return (html, error)."""
    try:
        import mammoth
    except ImportError:
        return "", "mammoth 패키지가 없습니다. (.venv에 mammoth 설치 필요)"
    try:
        with path.open("rb") as f:
            result = mammoth.convert_to_html(f)
        body = (result.value or "").strip()
        if not body:
            return "", "DOCX에서 표시할 HTML을 만들지 못했습니다."
        style = (
            "<style>"
            "body{font-family:'Malgun Gothic','Noto Sans KR',sans-serif;"
            "font-size:14px;line-height:1.55;color:#111;padding:8px;}"
            "table{border-collapse:collapse;width:100%;margin:12px 0;}"
            "td,th{border:1px solid #bbb;padding:6px 8px;vertical-align:top;}"
            "h1,h2,h3{margin:14px 0 8px;}"
            "p{margin:0 0 8px;}"
            "</style>"
        )
        return f"{style}{body}", ""
    except Exception as exc:  # noqa: BLE001
        return "", f"DOCX 변환 실패: {exc}"


def pdf_page_pngs(
    path: Path,
    *,
    max_pages: int = 20,
    scale: float = 1.6,
) -> tuple[list[bytes], int, str]:
    """Render PDF pages to PNG bytes for in-app preview.

    Edge/Chrome often block data-URI PDF iframes; page images avoid that.
    Returns (png_pages, total_page_count, error).
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return [], 0, "pypdfium2 패키지가 없습니다. (.venv에 pypdfium2 설치 필요)"
    try:
        size = path.stat().st_size
    except OSError as exc:
        return [], 0, f"PDF를 읽을 수 없습니다: {exc}"
    if size > 40 * 1024 * 1024:
        return [], 0, "PDF가 너무 큽니다(40MB+). 원본 다운로드로 확인해 주세요."
    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:  # noqa: BLE001
        return [], 0, f"PDF를 열 수 없습니다: {exc}"
    try:
        total = len(pdf)
        if total <= 0:
            return [], 0, "PDF 페이지가 없습니다."
        pages: list[bytes] = []
        limit = min(total, max(1, int(max_pages)))
        for i in range(limit):
            page = pdf[i]
            try:
                bitmap = page.render(scale=scale)
                pil = bitmap.to_pil()
                from io import BytesIO

                buf = BytesIO()
                pil.save(buf, format="PNG", optimize=True)
                pages.append(buf.getvalue())
            finally:
                page.close()
        return pages, total, ""
    except Exception as exc:  # noqa: BLE001
        return [], 0, f"PDF 미리보기 렌더 실패: {exc}"
    finally:
        try:
            pdf.close()
        except Exception:  # noqa: BLE001
            pass


def hwpx_preview_html(
    path: Path,
    *,
    max_pages: int = 12,
    max_file_bytes: int = 150 * 1024 * 1024,
    max_html_bytes: int = 8 * 1024 * 1024,
) -> tuple[str, str, str]:
    """Build approximate HWPX layout HTML for in-app preview.

    Returns (html, warning, error). Prefers layout page fragments (first N pages)
    so large packages can still preview without loading a huge full-document HTML.
    Falls back to HwpxDocument.export_html().
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        return "", "", f"HWPX를 읽을 수 없습니다: {exc}"
    if size > max_file_bytes:
        return (
            "",
            "",
            "HWPX가 너무 큽니다(150MB+). 원본 다운로드 후 한글에서 확인해 주세요.",
        )

    warning = ""
    limit = max(1, int(max_pages))

    # 1) Page-aware layout preview — show first N pages even for large files
    try:
        from hwpx.experimental import render_layout_preview

        preview = render_layout_preview(path, mode="pages", title=path.name)
        fragments = [str(f) for f in (preview.page_fragments or ()) if str(f).strip()]
        total = len(fragments) or len(getattr(preview, "pages", ()) or ())
        if fragments:
            shown = fragments[:limit]
            html = _hwpx_pages_to_viewer_html(path.name, shown)
            raw_len = len(html.encode("utf-8", errors="ignore"))
            if raw_len > max_html_bytes and len(shown) > 1:
                # Shrink page count until under budget
                for n in range(len(shown) - 1, 0, -1):
                    candidate = _hwpx_pages_to_viewer_html(path.name, shown[:n])
                    if len(candidate.encode("utf-8", errors="ignore")) <= max_html_bytes:
                        html = candidate
                        shown = shown[:n]
                        break
                else:
                    warning = "레이아웃 HTML이 커서 단순 HTML로 폴백합니다."
                    html = ""
            if html:
                if total > len(shown):
                    note = (
                        f"미리보기: 앞 {len(shown)} / 전체 {total}페이지 · "
                        "전체는 원본 다운로드로 확인하세요."
                    )
                else:
                    note = "한글 레이아웃 근사 미리보기 · 원본과 다를 수 있음"
                return html, note, ""
    except Exception as exc:  # noqa: BLE001
        warning = f"레이아웃 뷰어 실패 → 단순 HTML 시도 ({exc})"

    # 2) Lighter export_html fallback
    try:
        from hwpx import HwpxDocument

        doc = HwpxDocument.open(str(path))
        try:
            html = str(doc.export_html() or "").strip()
        finally:
            try:
                doc.close()
            except Exception:  # noqa: BLE001
                pass
        if not html:
            return "", warning, "HWPX에서 표시할 HTML을 만들지 못했습니다."
        if len(html.encode("utf-8", errors="ignore")) > max_html_bytes:
            return "", warning, "미리보기 HTML이 너무 큽니다. 원본 다운로드로 확인해 주세요."
        note = "단순 HTML 미리보기 · 원본 레이아웃과 다를 수 있음"
        if warning:
            note = f"{note} ({warning})"
        if "<html" not in html.lower():
            html = (
                "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'/>"
                "<style>body{font-family:'Malgun Gothic','Noto Sans KR',sans-serif;"
                "font-size:14px;line-height:1.5;color:#111;padding:12px;}"
                "table{border-collapse:collapse;}td,th{border:1px solid #bbb;padding:4px 6px;}"
                "</style></head><body>"
                f"{html}</body></html>"
            )
        return html, note, ""
    except Exception as exc:  # noqa: BLE001
        detail = warning or str(exc)
        return "", "", f"HWPX 미리보기 실패: {detail}"


def _hwpx_pages_to_viewer_html(title: str, page_fragments: list[str]) -> str:
    """Assemble page fragments into a scrollable viewer document."""
    body = "".join(page_fragments)
    try:
        from hwpx.tools.document_viewer import _viewer_html

        return str(_viewer_html(title, "pages", body, len(page_fragments)))
    except Exception:  # noqa: BLE001
        safe_title = html.escape(title or "HWPX")
        return (
            "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'/>"
            f"<title>{safe_title}</title>"
            "<style>html{background:#f2f3f5;}body{margin:0;padding:12px;"
            "font-family:'Malgun Gothic','Noto Sans KR',sans-serif;color:#111;}"
            ".hwpx-preview-page{background:#fff;margin:0 auto 16px;"
            "box-shadow:0 1px 3px rgba(0,0,0,.08);}</style>"
            f"</head><body>{body}</body></html>"
        )


def pdf_pages_preview_html(
    pages: list[bytes],
    *,
    max_width_px: int = 680,
    box_height_px: int = 640,
    attach_external_footer: bool = False,
) -> str:
    """Wrap rendered PDF page PNGs in a scrollable preview box."""
    parts: list[str] = []
    for i, png in enumerate(pages):
        b64 = base64.b64encode(png).decode("ascii")
        parts.append(
            "<div style='margin:0 auto 14px;text-align:center;max-width:"
            f"{max_width_px}px;'>"
            f"<img src='data:image/png;base64,{b64}' "
            f"alt='page {i + 1}' "
            "style='display:block;width:100%;height:auto;"
            "border:1px solid #e5e7eb;border-radius:4px;"
            "background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.04);'/>"
            f"<div style='font-size:12px;color:#6b7280;margin-top:6px;'>"
            f"{i + 1}페이지</div></div>"
        )
    inner = "".join(parts) or (
        "<p style='color:#6b7280;padding:12px;'>미리볼 페이지가 없습니다.</p>"
    )
    # Leave bottom open when footer is rendered outside components.html
    # (Streamlit iframes often clip in-iframe footers).
    if attach_external_footer:
        radius = "10px 10px 0 0"
        border_bottom = "border-bottom:none;"
    else:
        radius = "10px"
        border_bottom = ""
    return (
        f"<div style='border:1px solid #d1d5db;{border_bottom}"
        f"border-radius:{radius};overflow:hidden;background:#f3f4f6;'>"
        f"<div style='height:{box_height_px}px;overflow:auto;padding:14px 12px;'>"
        f"<div style='margin:0 auto;max-width:{max_width_px}px;'>{inner}</div>"
        "</div></div>"
    )


def pdf_iframe_html(path: Path, *, height: int = 720) -> tuple[str, str]:
    """Legacy data-URI iframe embed (often blocked by Edge). Prefer pdf_page_pngs."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return "", f"PDF를 읽을 수 없습니다: {exc}"
    if len(data) > 25 * 1024 * 1024:
        return "", "PDF가 너무 큽니다(25MB+). 원본 다운로드로 확인해 주세요."
    b64 = base64.b64encode(data).decode("ascii")
    # Blob URL avoids Edge's data:application/pdf iframe block in many cases.
    frame = f"""
<div id="rm-pdf-wrap" style="width:100%;height:{height}px;border:1px solid #ddd;border-radius:8px;overflow:hidden;">
  <iframe id="rm-pdf" width="100%" height="100%" style="border:0;" title="PDF preview"></iframe>
</div>
<script>
(function () {{
  try {{
    var b64 = "{b64}";
    var bin = atob(b64);
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    var blob = new Blob([bytes], {{ type: "application/pdf" }});
    var url = URL.createObjectURL(blob);
    var frame = document.getElementById("rm-pdf");
    if (frame) frame.src = url;
  }} catch (e) {{
    var wrap = document.getElementById("rm-pdf-wrap");
    if (wrap) wrap.innerHTML = "<p style='padding:16px;color:#b00020;'>브라우저 PDF 뷰어를 열 수 없습니다.</p>";
  }}
}})();
</script>
"""
    return frame, ""


def text_file_preview(path: Path, *, max_chars: int = 50000) -> tuple[str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return "", f"텍스트 파일을 읽을 수 없습니다: {exc}"
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n...(생략)..."
    return text, ""


def table_preview_records(path: Path, *, max_rows: int = 80) -> tuple[list[dict[str, Any]], str]:
    try:
        import pandas as pd
    except ImportError:
        return [], "pandas가 필요합니다."
    try:
        ext = path.suffix.lower()
        if ext == ".csv":
            df = pd.read_csv(path)
        else:
            df = pd.read_excel(path)
        if df.empty:
            return [], "표 데이터가 비어 있습니다."
        view = df.head(max_rows)
        return view.to_dict(orient="records"), ""
    except Exception as exc:  # noqa: BLE001
        return [], f"표 미리보기 실패: {exc}"


def safe_download_name(doc: dict[str, Any], path: Path | None = None) -> str:
    name = str(doc.get("filename") or "").strip()
    if name:
        return name
    if path is not None:
        return path.name
    return "document"
