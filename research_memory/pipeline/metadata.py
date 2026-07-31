from __future__ import annotations

import re
from pathlib import Path

from research_memory.schema import DocumentMetadata, Fact, TextChunk

_YEAR_RE = re.compile(r"\b(20\d{2}|19\d{2})\b")
_MONEY_RE = re.compile(
    r"((?:₩|\\)?\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?\s*(?:원|억|만원)?|\d+(?:\.\d+)?\s*(?:억원|만원|원))"
)
_PROJECT_RE = re.compile(
    r"(과제명|사업명|프로젝트명|Project)\s*[:：]?\s*(.+)",
    re.IGNORECASE,
)
_AUTHOR_RE = re.compile(
    r"(작성자|책임자|저자|Author)\s*[:：]?\s*(.+)",
    re.IGNORECASE,
)

_DOC_TYPE_HINTS = (
    ("proposal", ("제안서", "RFP", "공모", "사업계획")),
    ("paper", ("논문", "abstract", "references", "참고문헌")),
    ("note", ("연구노트", "research note", "실험노트")),
    ("meeting", ("회의록", "meeting minutes", "안건")),
    ("excel", ("sheet=", "columns=")),
)


def extract_metadata_and_facts(
    filename: str,
    file_type: str,
    chunks: list[TextChunk],
) -> tuple[DocumentMetadata, list[Fact]]:
    head = "\n".join(c.text for c in chunks[:8])
    full_sample = head[:8000]
    stem = Path(filename).stem

    year = _first_year(full_sample) or _first_year(stem)
    project = _first_match(_PROJECT_RE, full_sample) or ""
    authors_raw = _first_match(_AUTHOR_RE, full_sample)
    authors = [a.strip() for a in re.split(r"[,;/·]", authors_raw) if a.strip()] if authors_raw else []

    doc_type = "excel" if file_type == "excel" else _guess_doc_type(full_sample, filename)
    keywords = _keywords(full_sample)

    meta = DocumentMetadata(
        title=project or stem,
        project_id=project[:120],
        authors=authors[:8],
        year=year,
        doc_type=doc_type,
        keywords=keywords,
        source_filename=filename,
        extra={"file_type": file_type, "chunk_count": len(chunks)},
    )

    facts: list[Fact] = []
    if project:
        facts.append(Fact(label="project_name", value=project[:200], location="metadata", confidence=0.7))
    if year:
        facts.append(Fact(label="year", value=str(year), location="metadata", confidence=0.6))
    for author in authors[:5]:
        facts.append(Fact(label="author", value=author, location="metadata", confidence=0.55))

    for chunk in chunks[:20]:
        for money in _MONEY_RE.findall(chunk.text)[:3]:
            value = money if isinstance(money, str) else money[0]
            facts.append(
                Fact(
                    label="amount",
                    value=value.strip(),
                    location=chunk.location,
                    confidence=0.45,
                )
            )
        for line in chunk.text.splitlines():
            if ":" in line or "：" in line:
                parts = re.split(r"[:：]", line, maxsplit=1)
                if len(parts) == 2 and 1 < len(parts[0].strip()) <= 40:
                    label, value = parts[0].strip(), parts[1].strip()
                    if value and len(value) <= 200:
                        facts.append(
                            Fact(
                                label=label,
                                value=value,
                                location=chunk.location,
                                confidence=0.4,
                            )
                        )
            if len(facts) >= 40:
                break
        if len(facts) >= 40:
            break

    return meta, facts


def _first_year(text: str) -> int | None:
    m = _YEAR_RE.search(text or "")
    return int(m.group(1)) if m else None


def _first_match(pattern: re.Pattern[str], text: str) -> str:
    m = pattern.search(text or "")
    return m.group(2).strip() if m else ""


def _guess_doc_type(text: str, filename: str) -> str:
    blob = f"{filename}\n{text}".lower()
    for label, hints in _DOC_TYPE_HINTS:
        if any(h.lower() in blob for h in hints):
            return label
    return "other"


def _keywords(text: str, limit: int = 12) -> list[str]:
    tokens = re.findall(r"[A-Za-z가-힣][A-Za-z가-힣0-9_\-]{1,}", text)
    stop = {
        "그리고",
        "또는",
        "및",
        "위한",
        "대한",
        "있는",
        "없는",
        "한다",
        "있다",
        "this",
        "that",
        "with",
        "from",
        "were",
        "have",
    }
    freq: dict[str, int] = {}
    for tok in tokens:
        key = tok.lower()
        if key in stop or len(key) < 2:
            continue
        freq[key] = freq.get(key, 0) + 1
    ranked = sorted(freq.items(), key=lambda x: (-x[1], x[0]))
    return [w for w, _ in ranked[:limit]]
