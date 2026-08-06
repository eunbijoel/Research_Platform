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
) -> ChatAnswer:
    repo = repo or KnowledgeRepository()
    excluded = list(exclude_project_ids or [])
    similar_intent = bool(_SIMILAR_INTENT.search(question or ""))
    if similar_intent and not excluded:
        excluded = _resolve_focus_projects(question, repo)

    citations = retrieve(
        question,
        repo=repo,
        top_k=top_k,
        exclude_project_ids=excluded or None,
    )
    if not citations:
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
            )
            text = generate_text(prompt)
            if not text.strip():
                return _extractive(question, citations)
            return ChatAnswer(answer=text.strip(), citations=citations, mode="llm")
        except LLMConnectionError:
            return _extractive(question, citations)

    return _extractive(question, citations)


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
) -> str:
    evidence_blocks = []
    for i, c in enumerate(citations, start=1):
        evidence_blocks.append(
            f"[{i}] file={c.filename} | location={c.location} | score={c.score:.3f}\n{c.snippet}"
        )
    evidence = "\n\n".join(evidence_blocks)
    extra = ""
    if similar_intent or excluded_projects:
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
