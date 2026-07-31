from __future__ import annotations

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


def answer_question(
    question: str,
    *,
    repo: KnowledgeRepository | None = None,
    top_k: int = 6,
) -> ChatAnswer:
    repo = repo or KnowledgeRepository()
    citations = retrieve(question, repo=repo, top_k=top_k)
    if not citations:
        return ChatAnswer(
            answer="메모리에 근거가 없어 답할 수 없습니다. 관련 문서를 먼저 인제스트하세요.",
            citations=[],
            refused=True,
            mode="refused",
        )

    if llm_available():
        try:
            prompt = _build_prompt(question, citations)
            text = generate_text(prompt)
            if not text.strip():
                return _extractive(question, citations)
            return ChatAnswer(answer=text.strip(), citations=citations, mode="llm")
        except LLMConnectionError:
            return _extractive(question, citations)

    return _extractive(question, citations)


def _build_prompt(question: str, citations: list[Citation]) -> str:
    evidence_blocks = []
    for i, c in enumerate(citations, start=1):
        evidence_blocks.append(
            f"[{i}] file={c.filename} | location={c.location} | score={c.score:.3f}\n{c.snippet}"
        )
    evidence = "\n\n".join(evidence_blocks)
    return (
        f"{_SYSTEM}\n\n"
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
