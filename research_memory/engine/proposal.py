from __future__ import annotations

import json
import re
from datetime import datetime
from io import BytesIO
from typing import Any

from docx import Document

from research_memory.engine.llm import LLMConnectionError, generate_text, llm_available
from research_memory.engine.retrieval import retrieve
from research_memory.kb.repository import KnowledgeRepository
from research_memory.pipeline.chunking import refine_chunks
from research_memory.pipeline.extractors import extract_chunks
from research_memory.schema import Citation

NOT_FOUND = "확인 필요"

RFP_FIELDS = [
    "project_name",
    "purpose",
    "organization",
    "duration",
    "budget",
    "mandatory_requirements",
    "tech_requirements",
    "kpi",
    "consortium_conditions",
    "evaluation_criteria",
    "submission_documents",
    "notes",
]

DRAFT_KEYS = [
    "necessity",
    "center_role",
    "work_details",
    "yearly_plan",
    "deliverables",
    "kpi_draft",
    "consortium_role",
    "expected_effects",
    "open_questions",
]

DRAFT_LABELS = {
    "necessity": "참여 필요성",
    "center_role": "우리 센터의 담당 역할",
    "work_details": "세부 수행내용",
    "yearly_plan": "연차별 수행계획",
    "deliverables": "예상 산출물",
    "kpi_draft": "KPI 초안",
    "consortium_role": "컨소시엄 내 역할",
    "expected_effects": "기대효과",
    "open_questions": "추가 확인이 필요한 사항",
}


def parse_rfp_bytes(data: bytes, filename: str) -> tuple[list[dict[str, str]], str]:
    """Extract citeable RFP chunks from an uploaded file."""
    from pathlib import Path
    import tempfile

    suffix = Path(filename).suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        path = Path(tmp.name)
    try:
        _ftype, raw, err = extract_chunks(path)
        if err and not raw:
            return [], err
        chunks = refine_chunks(raw)
        out = [
            {"file": filename, "location": c.location, "text": c.text}
            for c in chunks
            if c.text.strip()
        ]
        return out, "" if out else (err or "No text extracted from RFP")
    finally:
        path.unlink(missing_ok=True)


def analyze_rfp(rfp_chunks: list[dict[str, str]]) -> dict[str, Any]:
    source = _chunks_to_text(rfp_chunks)
    if not source.strip():
        result = _empty_rfp()
        result["error"] = "RFP text is empty"
        return result

    if not llm_available():
        return _heuristic_rfp(source, rfp_chunks)

    prompt = f"""당신은 공공 R&D/용역 공고문을 분석하는 보조 도구입니다.
아래 [RFP 원문]에서만 근거를 찾아 JSON 객체 하나만 출력하세요.
설명 문장이나 마크다운 코드블록은 넣지 마세요.

각 항목:
- project_name, purpose, organization, duration, budget, consortium_conditions, notes: 문자열
- mandatory_requirements, tech_requirements, kpi, evaluation_criteria, submission_documents: 문자열 리스트
- 근거가 없으면 "{NOT_FOUND}" (리스트는 ["{NOT_FOUND}"])
- mandatory_requirements에는 실제 과업/수행내용만. 입찰자격은 notes 등으로.

[RFP 원문]
{source}
"""
    try:
        raw = generate_text(prompt)
        return _parse_rfp_json(raw)
    except LLMConnectionError as exc:
        result = _heuristic_rfp(source, rfp_chunks)
        result["error"] = str(exc)
        result["mode"] = "heuristic_fallback"
        return result


def gather_kb_evidence(
    rfp: dict[str, Any],
    *,
    repo: KnowledgeRepository | None = None,
    top_k_per_query: int = 4,
    project_id: str | None = None,
) -> list[Citation]:
    """Retrieve Memory evidence for RFP requirements (Phase 3 core)."""
    repo = repo or KnowledgeRepository()
    queries: list[str] = []
    for key in ("project_name", "purpose"):
        val = rfp.get(key)
        if isinstance(val, str) and val and val != NOT_FOUND:
            queries.append(val)
    for key in ("mandatory_requirements", "tech_requirements", "kpi"):
        items = rfp.get(key) or []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, str) and item and item != NOT_FOUND:
                    queries.append(item)
        elif isinstance(items, str) and items != NOT_FOUND:
            queries.append(items)

    if not queries:
        queries = ["연구 역량 제안서 산출물 센터 역할"]

    seen: set[tuple[str, str, str]] = set()
    citations: list[Citation] = []
    for q in queries[:12]:
        for c in retrieve(q, repo=repo, top_k=top_k_per_query):
            if project_id:
                doc = repo.get_document(c.document_id) or {}
                if (doc.get("project_id") or "") != project_id:
                    continue
            key = (c.document_id, c.location, c.snippet[:80])
            if key in seen:
                continue
            seen.add(key)
            citations.append(c)
    citations.sort(key=lambda x: -x.score)
    return citations[:24]


def suggest_roles(
    rfp: dict[str, Any],
    evidence: list[Citation],
) -> list[dict[str, Any]]:
    evidence_text = _evidence_text(evidence)
    if not llm_available():
        return _heuristic_roles(rfp, evidence)

    prompt = f"""당신은 공공 R&D 컨소시엄 제안서 작성을 돕는 보조 도구입니다.
[RFP 분석]과 [Knowledge Base 근거]만 사용해 우리 센터 역할 후보를 최대 3개 제안하세요.
JSON 배열만 출력. 각 원소 키: role, reason, related_requirements, evidence, open_questions (문자열)

규칙:
- evidence는 KB 근거에 실제로 있는 내용만, 파일명을 함께 표시. 없으면 "{NOT_FOUND}"
- 근거 없는 추론에는 "[AI 제안]" 표시
- 전체 제안서 완성이 아니라 우리 센터 파트만

[RFP 분석]
{json.dumps(rfp, ensure_ascii=False, indent=2)}

[Knowledge Base 근거]
{evidence_text}
"""
    try:
        raw = generate_text(prompt)
        return _parse_role_list(raw)
    except LLMConnectionError as exc:
        roles = _heuristic_roles(rfp, evidence)
        if roles:
            roles[0]["open_questions"] = str(exc)
        return roles


def generate_draft(
    rfp: dict[str, Any],
    selected_role: dict[str, Any],
    evidence: list[Citation],
) -> dict[str, Any]:
    evidence_text = _evidence_text(evidence)
    if not llm_available():
        return _heuristic_draft(rfp, selected_role, evidence)

    prompt = f"""당신은 공공 R&D 컨소시엄 제안서의 "우리 센터 담당 파트" 초안 작성 보조 도구입니다.
전체 제안서를 완성하지 말고 우리 센터 부분만 작성하세요.
JSON 객체만 출력. 키: {", ".join(DRAFT_KEYS)} (모두 문자열)

규칙:
- 문장 앞에 [자료 근거], [AI 제안], [확인 필요] 중 하나
- [자료 근거]는 KB 근거에 실제 있는 내용만, 가능하면 파일명 표기
- 수치·예산·기업명은 KB에 없으면 [확인 필요]
- 과거 문장 복붙 금지, 이번 RFP에 맞게 작성

[RFP 분석]
{json.dumps(rfp, ensure_ascii=False, indent=2)}

[선택한 역할]
{json.dumps(selected_role, ensure_ascii=False, indent=2)}

[Knowledge Base 근거]
{evidence_text}
"""
    try:
        raw = generate_text(prompt)
        draft = _parse_draft(raw)
        draft["citations"] = [c.to_dict() for c in evidence]
        draft["mode"] = "llm"
        return draft
    except LLMConnectionError as exc:
        draft = _heuristic_draft(rfp, selected_role, evidence)
        draft["error"] = str(exc)
        return draft


def run_proposal_pipeline(
    rfp_data: bytes,
    rfp_filename: str,
    *,
    repo: KnowledgeRepository | None = None,
    project_id: str | None = None,
    selected_role_index: int = 0,
) -> dict[str, Any]:
    """End-to-end: parse → analyze → KB evidence → roles → draft."""
    repo = repo or KnowledgeRepository()
    chunks, err = parse_rfp_bytes(rfp_data, rfp_filename)
    if err and not chunks:
        return {"ok": False, "error": err}

    rfp = analyze_rfp(chunks)
    evidence = gather_kb_evidence(rfp, repo=repo, project_id=project_id)
    roles = suggest_roles(rfp, evidence)
    if not roles:
        roles = [{"role": NOT_FOUND, "reason": NOT_FOUND, "related_requirements": NOT_FOUND, "evidence": NOT_FOUND, "open_questions": "역할 후보 없음"}]
    idx = max(0, min(selected_role_index, len(roles) - 1))
    selected = roles[idx]
    draft = generate_draft(rfp, selected, evidence)
    md = build_markdown(rfp, selected, draft, evidence)
    return {
        "ok": True,
        "rfp": rfp,
        "evidence": [c.to_dict() for c in evidence],
        "roles": roles,
        "selected_role": selected,
        "draft": draft,
        "markdown": md,
        "parse_error": err,
    }


def build_markdown(
    rfp: dict[str, Any],
    selected_role: dict[str, Any],
    draft: dict[str, Any],
    evidence: list[Citation] | list[dict[str, Any]] | None = None,
) -> str:
    lines = [
        f"# 제안 초안 - {rfp.get('project_name', NOT_FOUND)}",
        "",
        f"_생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M')}_",
        "",
        "## 1. RFP 핵심 정보",
        f"- 사업 목적: {rfp.get('purpose', NOT_FOUND)}",
        f"- 발주기관: {rfp.get('organization', NOT_FOUND)}",
        f"- 사업기간: {rfp.get('duration', NOT_FOUND)}",
        f"- 예산: {rfp.get('budget', NOT_FOUND)}",
        "",
        "## 2. 선택한 역할 후보",
        f"- 역할명: {selected_role.get('role', NOT_FOUND)}",
        f"- 추천 이유: {selected_role.get('reason', NOT_FOUND)}",
        "",
        "## 3. 우리 센터 담당 제안 초안",
    ]
    for key, label in DRAFT_LABELS.items():
        lines.append(f"### {label}")
        lines.append(str(draft.get(key, NOT_FOUND)))
        lines.append("")

    if evidence:
        lines.append("## 4. Knowledge Base 근거")
        for i, c in enumerate(evidence, start=1):
            if isinstance(c, Citation):
                lines.append(f"[{i}] {c.filename} / {c.location} (score={c.score:.3f})")
                lines.append(c.snippet)
            else:
                lines.append(
                    f"[{i}] {c.get('filename')} / {c.get('location')} "
                    f"(score={float(c.get('score', 0)):.3f})"
                )
                lines.append(str(c.get("snippet", "")))
            lines.append("")

    lines.extend(
        [
            "---",
            "※ AI 초안입니다. `[확인 필요]` 항목은 담당자 검토가 필요합니다. "
            "전체 제안서 자동 완성이 아니라 Research Memory 근거 기반 재사용 초안입니다.",
        ]
    )
    return "\n".join(lines)


def export_docx_bytes(
    rfp: dict[str, Any],
    selected_role: dict[str, Any],
    draft: dict[str, Any],
) -> bytes:
    document = Document()
    document.add_heading(f"제안 초안 - {rfp.get('project_name', NOT_FOUND)}", level=0)
    document.add_paragraph(f"생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    document.add_heading("선택한 역할", level=1)
    document.add_paragraph(f"역할: {selected_role.get('role', NOT_FOUND)}")
    document.add_paragraph(f"이유: {selected_role.get('reason', NOT_FOUND)}")
    document.add_heading("우리 센터 담당 초안", level=1)
    for key, label in DRAFT_LABELS.items():
        document.add_heading(label, level=2)
        document.add_paragraph(str(draft.get(key, NOT_FOUND)))
    buf = BytesIO()
    document.save(buf)
    return buf.getvalue()


# --- helpers ---

def _chunks_to_text(chunks: list[dict[str, str]], max_chars: int = 14000) -> str:
    parts: list[str] = []
    total = 0
    for ch in chunks:
        piece = f"[출처: {ch.get('file')} / {ch.get('location')}]\n{ch.get('text', '')}\n"
        if total + len(piece) > max_chars:
            parts.append("\n...(생략)...")
            break
        parts.append(piece)
        total += len(piece)
    return "\n".join(parts)


def _evidence_text(evidence: list[Citation]) -> str:
    if not evidence:
        return "(Knowledge Base 근거 없음 — 문서를 먼저 인제스트하세요)"
    blocks = []
    for i, c in enumerate(evidence, start=1):
        blocks.append(
            f"[{i}] file={c.filename} | location={c.location} | score={c.score:.3f}\n{c.snippet}"
        )
    return "\n\n".join(blocks)


def _empty_rfp() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in RFP_FIELDS:
        if field in {
            "mandatory_requirements",
            "tech_requirements",
            "kpi",
            "evaluation_criteria",
            "submission_documents",
        }:
            result[field] = [NOT_FOUND]
        else:
            result[field] = NOT_FOUND
    return result


def _parse_rfp_json(raw: str) -> dict[str, Any]:
    cleaned = _strip_fence(raw)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        result = _empty_rfp()
        result["error"] = "LLM RFP JSON parse failed"
        result["raw_response"] = raw[:1000]
        return result
    result = _empty_rfp()
    for field in RFP_FIELDS:
        if field in parsed and parsed[field]:
            result[field] = parsed[field]
    result["mode"] = "llm"
    return result


def _heuristic_rfp(source: str, chunks: list[dict[str, str]]) -> dict[str, Any]:
    result = _empty_rfp()
    head = source[:2000]
    m = re.search(r"(과제명|사업명)\s*[:：]?\s*(.+)", head)
    if m:
        result["project_name"] = m.group(2).strip()[:200]
    else:
        result["project_name"] = chunks[0]["file"] if chunks else NOT_FOUND
    result["purpose"] = head[:400].replace("\n", " ")
    result["mandatory_requirements"] = [
        line.strip(" -•\t")
        for line in source.splitlines()
        if any(k in line for k in ("수행", "구축", "개발", "분석", "제안"))
    ][:8] or [NOT_FOUND]
    result["notes"] = "LLM offline — heuristic RFP parse"
    result["mode"] = "heuristic"
    return result


def _heuristic_roles(rfp: dict[str, Any], evidence: list[Citation]) -> list[dict[str, Any]]:
    ev = (
        f"[자료 근거] {evidence[0].filename}: {evidence[0].snippet[:160]}"
        if evidence
        else NOT_FOUND
    )
    reqs = rfp.get("mandatory_requirements") or []
    related = ", ".join(reqs[:3]) if isinstance(reqs, list) else str(reqs)
    return [
        {
            "role": "[AI 제안] Document Intelligence / Research Memory 담당",
            "reason": f"[AI 제안] RFP 요구({related})와 센터 문서지능 역량 연계",
            "related_requirements": related or NOT_FOUND,
            "evidence": ev,
            "open_questions": "주관기관 역할 분담 확인 필요",
        }
    ]


def _heuristic_draft(
    rfp: dict[str, Any],
    selected_role: dict[str, Any],
    evidence: list[Citation],
) -> dict[str, Any]:
    cite = (
        f"[자료 근거] ({evidence[0].filename}) {evidence[0].snippet[:220]}"
        if evidence
        else f"[확인 필요] KB 근거 없음"
    )
    draft = {key: f"[확인 필요] LLM offline — {key}" for key in DRAFT_KEYS}
    draft["necessity"] = (
        f"[자료 근거] 사업명: {rfp.get('project_name', NOT_FOUND)}. "
        f"Memory 기반 재사용으로 제안 효율을 높일 필요가 있음."
    )
    draft["center_role"] = (
        f"[자료 근거] 역할: {selected_role.get('role', NOT_FOUND)}. {cite}"
    )
    draft["work_details"] = cite
    draft["open_questions"] = "[확인 필요] LLM 연결 후 초안 품질을 재생성하세요."
    draft["citations"] = [c.to_dict() for c in evidence]
    draft["mode"] = "heuristic"
    return draft


def _parse_role_list(raw: str) -> list[dict[str, Any]]:
    cleaned = _strip_fence(raw)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed[:3]
    except json.JSONDecodeError:
        pass
    return [
        {
            "role": "확인 필요 (응답 해석 실패)",
            "reason": "LLM 응답을 JSON으로 해석하지 못했습니다.",
            "related_requirements": NOT_FOUND,
            "evidence": NOT_FOUND,
            "open_questions": raw[:500],
        }
    ]


def _parse_draft(raw: str) -> dict[str, Any]:
    cleaned = _strip_fence(raw)
    try:
        parsed = json.loads(cleaned)
        return {key: parsed.get(key, NOT_FOUND) for key in DRAFT_KEYS}
    except json.JSONDecodeError:
        result = {key: NOT_FOUND for key in DRAFT_KEYS}
        result["open_questions"] = raw[:500]
        result["error"] = "LLM draft JSON parse failed"
        return result


def _strip_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    return cleaned
