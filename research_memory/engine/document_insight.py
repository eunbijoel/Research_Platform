"""Document Insight MVP: type / role-summary / topics / recommended uses."""

from __future__ import annotations

import json
import re
from typing import Any

from research_memory.engine.llm import LLMConnectionError, generate_text, llm_available

ALLOWED_DOC_TYPES = {
    "proposal",
    "report",
    "paper",
    "patent",
    "regulation",
    "note",
    "manual",
    "rfp",
    "other",
}

ALLOWED_USES = [
    "Research Chat",
    "Proposal Reference",
    "Similarity Analysis",
    "Compliance Check",
    "Technology Classification",
    "Research Note",
]

_DOC_TYPE_LABELS = {
    "proposal": "제안서",
    "report": "보고서",
    "paper": "논문",
    "patent": "특허",
    "regulation": "규정/운영요령",
    "note": "연구노트",
    "manual": "매뉴얼",
    "rfp": "RFP/공고문",
    "other": "기타",
}


def doc_type_label(doc_type: str) -> str:
    key = (doc_type or "other").strip().lower()
    return _DOC_TYPE_LABELS.get(key, key or "기타")


def get_document_insight(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    insight = meta.get("document_insight")
    return insight if isinstance(insight, dict) else None


def generate_document_insight(
    text: str,
    *,
    filename: str = "",
    existing_doc_type: str = "",
) -> dict[str, Any] | None:
    """
    Ask LLM for a compact Document Insight JSON.
    Returns normalized dict or None on failure / unavailable LLM.
    """
    body = (text or "").strip()
    if not body:
        return None
    if not llm_available():
        return None

    prefer = _prefer_doc_type(existing_doc_type)
    uses_line = ", ".join(ALLOWED_USES)
    types_line = ", ".join(sorted(ALLOWED_DOC_TYPES))
    sample = body[:7000]

    prompt = f"""당신은 Research Memory(조직 연구 기억) 플랫폼의 문서 분석 보조입니다.
아래 문서를 읽고 JSON 객체 하나만 출력하세요. 설명 문장이나 마크다운 코드블록은 넣지 마세요.

필드:
- document_type: 다음 중 하나 → {types_line}
- summary: 한국어 1~2문장.
  중요: 문서 내용을 나열해 요약하지 마세요.
  대신 "이 문서가 어떤 역할을 하는 문서이며, 왜 Research Memory에 저장해 두어야 하는지"를 설명하세요.
- key_topics: 짧은 명사형 키워드 3~6개 (문자열 배열)
- recommended_uses: 아래 목록에서만 1~4개 선택 (문자열 배열, 목록 밖 값 금지)
  {uses_line}

규칙:
- 기존 추정 유형이 있으면 우선 고려: {prefer or "(없음)"}
- recommended_uses는 위 허용 목록의 문자열을 그대로 사용
- 원문에 근거가 약한 내용은 만들지 말 것

파일명: {filename}

[문서 발췌]
{sample}
"""
    try:
        raw = generate_text(prompt)
    except LLMConnectionError:
        return None
    return parse_document_insight(raw, existing_doc_type=prefer)


def parse_document_insight(
    raw: str,
    *,
    existing_doc_type: str = "",
) -> dict[str, Any] | None:
    cleaned = _strip_fence(raw or "")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    prefer = _prefer_doc_type(existing_doc_type)
    dtype = str(data.get("document_type") or "").strip().lower()
    if dtype not in ALLOWED_DOC_TYPES:
        dtype = prefer or "other"
    elif prefer and prefer != "other" and dtype == "other":
        dtype = prefer

    summary = str(data.get("summary") or "").strip()
    if not summary:
        return None
    # keep one-line-ish
    summary = re.sub(r"\s+", " ", summary).strip()
    if len(summary) > 280:
        summary = summary[:277] + "…"

    topics_raw = data.get("key_topics") or []
    topics: list[str] = []
    if isinstance(topics_raw, list):
        for t in topics_raw:
            s = re.sub(r"\s+", " ", str(t).strip())
            if s and s not in topics:
                topics.append(s)
            if len(topics) >= 6:
                break
    if len(topics) < 3:
        # allow 1–2 if model returns fewer, but prefer at least 1
        if not topics:
            return None

    uses_raw = data.get("recommended_uses") or []
    uses: list[str] = []
    allowed_set = set(ALLOWED_USES)
    if isinstance(uses_raw, list):
        for u in uses_raw:
            s = str(u).strip()
            if s in allowed_set and s not in uses:
                uses.append(s)
            if len(uses) >= 4:
                break
    if not uses:
        return None

    return {
        "document_type": dtype,
        "summary": summary,
        "key_topics": topics[:6],
        "recommended_uses": uses,
    }


def _prefer_doc_type(existing: str) -> str:
    val = (existing or "").strip().lower()
    if val in ALLOWED_DOC_TYPES and val not in {"other", "unknown", ""}:
        return val
    return ""


def _strip_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    # sometimes model wraps with prose; try to find first {...}
    if not cleaned.startswith("{"):
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            cleaned = m.group(0)
    return cleaned
