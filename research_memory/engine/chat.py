from __future__ import annotations

import re

from research_memory.engine.llm import LLMConnectionError, generate_text, llm_available
from research_memory.engine.retrieval import retrieve
from research_memory.kb.repository import KnowledgeRepository
from research_memory.schema import ChatAnswer, Citation


_SYSTEM = """당신은 센터 Research Memory의 질의응답 엔진입니다.
규칙:
1) 반드시 제공된 근거(Evidence)만 사용합니다.
2) 근거에 없으면 "메모리에 근거가 없어 답할 수 없습니다"라고 거절합니다.
3) 답변 끝에 사용한 근거를 [1], [2] 형식으로 표시합니다.
4) 추측·일반 지식으로 메우지 않습니다.
"""

_SIMILAR_INTENT = re.compile(
    r"(유사|비슷한|추천|관련성|관련\s*높|재사용\s*가능|비슷한\s*과제|유사한\s*과제|"
    r"가장\s*관련|비교\s*할|활용\s*할\s*수\s*있는\s*부분|"
    r"similar|recommend)",
    re.IGNORECASE,
)


def answer_question(
    question: str,
    *,
    repo: KnowledgeRepository | None = None,
    top_k: int = 6,
    exclude_project_ids: list[str] | None = None,
    document_id: str | None = None,
    document_ids: list[str] | None = None,
) -> ChatAnswer:
    repo = repo or KnowledgeRepository()
    excluded = list(exclude_project_ids or [])
    focus_ids = _normalize_document_ids(document_id=document_id, document_ids=document_ids)
    similar_intent = bool(_SIMILAR_INTENT.search(question or ""))
    if similar_intent and not excluded and not focus_ids:
        excluded = _resolve_focus_projects(question, repo)

    focus_doc = repo.get_document(focus_ids[0]) if len(focus_ids) == 1 else None
    focus_docs = [repo.get_document(did) for did in focus_ids] if focus_ids else []
    focus_docs = [d for d in focus_docs if d]

    citations = _retrieve_scoped(
        question,
        repo=repo,
        top_k=top_k,
        exclude_project_ids=excluded or None,
        document_ids=focus_ids,
    )
    if not citations:
        if focus_docs:
            names = ", ".join(
                f"`{d.get('filename') or d.get('id')}`" for d in focus_docs[:3]
            )
            return ChatAnswer(
                answer=(
                    f"{names}에서 검색할 텍스트 조각을 찾지 못했습니다. "
                    "문서가 ready 상태인지, 추출 텍스트가 있는지 Library에서 확인해 주세요."
                ),
                citations=[],
                refused=True,
                mode="refused",
            )
        return ChatAnswer(
            answer="메모리에 근거가 없어 답할 수 없습니다. 관련 문서를 먼저 인제스트하세요.",
            citations=[],
            refused=True,
            mode="refused",
        )

    if llm_available():
        try:
            prompt = _build_prompt(
                question,
                citations,
                excluded_projects=excluded,
                similar_intent=similar_intent,
                focus_document=focus_doc,
                focus_documents=focus_docs if len(focus_ids) > 1 else None,
            )
            text = generate_text(prompt)
            if not text.strip():
                return _extractive(question, citations)
            return ChatAnswer(answer=text.strip(), citations=citations, mode="llm")
        except LLMConnectionError:
            return _extractive(question, citations)

    return _extractive(question, citations)


def _normalize_document_ids(
    *,
    document_id: str | None,
    document_ids: list[str] | None,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in list(document_ids or []) + ([document_id] if document_id else []):
        did = (raw or "").strip()
        if not did or did in seen:
            continue
        seen.add(did)
        out.append(did)
    return out


def _retrieve_scoped(
    question: str,
    *,
    repo: KnowledgeRepository,
    top_k: int,
    exclude_project_ids: list[str] | None,
    document_ids: list[str],
) -> list[Citation]:
    if not document_ids:
        return retrieve(
            question,
            repo=repo,
            top_k=top_k,
            exclude_project_ids=exclude_project_ids,
        )
    if len(document_ids) == 1:
        return retrieve(
            question,
            repo=repo,
            top_k=top_k,
            exclude_project_ids=exclude_project_ids,
            document_id=document_ids[0],
        )
    per = max(2, (top_k + len(document_ids) - 1) // len(document_ids))
    merged: list[Citation] = []
    seen: set[tuple[str, str, str]] = set()
    for did in document_ids:
        for cite in retrieve(
            question,
            repo=repo,
            top_k=per,
            exclude_project_ids=None,
            document_id=did,
        ):
            key = (cite.document_id, cite.location or "", (cite.snippet or "")[:80])
            if key in seen:
                continue
            seen.add(key)
            merged.append(cite)
    merged.sort(key=lambda c: float(c.score or 0.0), reverse=True)
    return merged[:top_k]


def _resolve_focus_projects(question: str, repo: KnowledgeRepository) -> list[str]:
    """Match project folders mentioned in the question (longest id/title first)."""
    q = (question or "").strip()
    if not q:
        return []
    q_lower = q.lower()
    projects = repo.list_projects()
    ranked = sorted(
        projects,
        key=lambda p: max(
            len((p.get("project_id") or "").strip()),
            len((p.get("title") or "").strip()),
        ),
        reverse=True,
    )
    matched: list[str] = []
    for p in ranked:
        pid = (p.get("project_id") or "").strip()
        title = (p.get("title") or "").strip()
        if not pid:
            continue
        if pid.lower() in q_lower:
            matched.append(pid)
            continue
        if title and len(title) >= 4 and title.lower() in q_lower:
            matched.append(pid)
            continue
        # common short alias: AIDC ↔ 온프레미스 AIDC
        if "aidc" in q_lower and "aidc" in pid.lower():
            matched.append(pid)
    # de-dupe preserve order
    out: list[str] = []
    seen: set[str] = set()
    for pid in matched:
        if pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def _build_prompt(
    question: str,
    citations: list[Citation],
    *,
    excluded_projects: list[str] | None = None,
    similar_intent: bool = False,
    focus_document: dict | None = None,
    focus_documents: list[dict] | None = None,
) -> str:
    evidence_blocks = []
    for i, c in enumerate(citations, start=1):
        evidence_blocks.append(
            f"[{i}] file={c.filename} | location={c.location} | score={c.score:.3f}\n{c.snippet}"
        )
    evidence = "\n\n".join(evidence_blocks)
    extra = ""
    if focus_documents:
        names = ", ".join(
            f"`{d.get('title') or d.get('filename') or d.get('id')}`"
            for d in focus_documents[:5]
        )
        extra = (
            "\n추가 규칙 (첨부 문서 집중):\n"
            f"- Evidence는 일정에 첨부된 문서들({names})에서만 가져왔습니다.\n"
            "- 반드시 이 문서 내용만 사용해 답하세요. 다른 문서를 언급하지 마세요.\n"
            "- Evidence에 내용이 있으면 요약·정리·질문 응답을 수행하세요.\n"
        )
    elif focus_document:
        title = focus_document.get("title") or focus_document.get("filename") or "선택 문서"
        extra = (
            "\n추가 규칙 (선택 문서 집중):\n"
            f"- Evidence는 사용자가 Library에서 선택한 문서 `{title}`에서만 가져왔습니다.\n"
            "- 반드시 이 문서 내용만 사용해 답하세요. 다른 문서를 언급하지 마세요.\n"
            "- Evidence에 내용이 있으면 요약·정리·질문 응답을 수행하세요.\n"
        )
    elif similar_intent or excluded_projects:
        names = ", ".join(excluded_projects) if excluded_projects else "(질문의 대상 과제)"
        extra = (
            "\n추가 규칙 (유사/추천 질문):\n"
            f"- 대상 프로젝트 자신({names})은 추천하지 마세요.\n"
            "- 다른 센터 과제만 순위·이유로 추천하고, 재사용 가능한 기술/문서를 근거와 함께 제시하세요.\n"
            "- 대상 과제의 RFP·연구노트를 '유사 과제'처럼 포장하지 마세요.\n"
        )
    return (
        f"{_SYSTEM}\n"
        f"{extra}\n"
        f"질문:\n{question}\n\n"
        f"Evidence:\n{evidence}\n\n"
        "답변:"
    )


def _extractive(question: str, citations: list[Citation]) -> ChatAnswer:
    lines = [
        f"질문: {question}",
        "",
        "LLM 미연결 — 검색된 근거를 그대로 제시합니다.",
        "",
    ]
    for i, c in enumerate(citations, start=1):
        lines.append(f"[{i}] {c.filename} / {c.location} (score={c.score:.3f})")
        lines.append(c.snippet)
        lines.append("")
    lines.append("위 근거만으로 판단하세요. 근거 밖 내용은 확정하지 마세요.")
    return ChatAnswer(
        answer="\n".join(lines).strip(),
        citations=citations,
        refused=False,
        mode="extractive",
    )
