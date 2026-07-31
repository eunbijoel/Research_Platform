from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_memory.config import MODEL_NAME, MOCK_LLM, ensure_data_dirs
from research_memory.engine.chat import answer_question
from research_memory.engine.llm import llm_available
from research_memory.kb.repository import KnowledgeRepository
from research_memory.pipeline.ingest import ingest_bytes

st.set_page_config(
    page_title="Research Memory Platform",
    page_icon="🧠",
    layout="wide",
)

ensure_data_dirs()
repo = KnowledgeRepository()


def main() -> None:
    st.title("Research Memory Platform")
    st.caption(
        "An Organizational Research Intelligence Platform — Phase 1 Memory MVP "
        "(Pipeline → Metadata/Facts → KB → Chat with citations)"
    )

    with st.sidebar:
        st.subheader("Status")
        docs = repo.list_documents()
        ready = sum(1 for d in docs if d["status"] == "ready")
        failed = sum(1 for d in docs if d["status"] == "failed")
        st.metric("Documents", len(docs))
        st.write(f"ready={ready} · failed={failed}")
        st.write(f"LLM: {'connected' if llm_available() else 'offline/extractive'}")
        st.write(f"model={MODEL_NAME}")
        if MOCK_LLM:
            st.warning("RM_MOCK_LLM=true")
        st.divider()
        st.markdown(
            "**Phase 1 scope**\n"
            "- Ingest PDF/DOCX/TXT/MD/CSV/XLSX/HWPX\n"
            "- Metadata / Facts extraction\n"
            "- Knowledge Base + TF-IDF retrieval\n"
            "- Research Chat with mandatory citations"
        )

    tab_chat, tab_ingest, tab_library, tab_facts = st.tabs(
        ["Research Chat", "Ingest", "Library", "Facts"]
    )

    with tab_ingest:
        _ingest_tab()
    with tab_library:
        _library_tab()
    with tab_facts:
        _facts_tab()
    with tab_chat:
        _chat_tab()


def _ingest_tab() -> None:
    st.subheader("Document Intelligence → Metadata/Facts → Knowledge Base")
    project_id = st.text_input("Project ID (optional)", placeholder="e.g. KETI-2026-001")
    uploads = st.file_uploader(
        "Upload research assets",
        type=["pdf", "docx", "txt", "md", "csv", "xlsx", "xls", "hwpx"],
        accept_multiple_files=True,
    )
    if st.button("Ingest into Memory", type="primary", disabled=not uploads):
        for f in uploads or []:
            with st.spinner(f"Ingesting {f.name}…"):
                result = ingest_bytes(
                    f.getvalue(),
                    f.name,
                    repo=repo,
                    project_id=project_id.strip(),
                )
            if result.get("ok"):
                if result.get("skipped"):
                    st.info(f"{f.name}: already ingested")
                else:
                    st.success(
                        f"{f.name}: chunks={result.get('chunks')} facts={result.get('facts')}"
                    )
                    with st.expander(f"Metadata — {f.name}"):
                        st.json(result.get("metadata") or {})
            else:
                st.error(f"{f.name}: {result.get('error', 'failed')}")


def _library_tab() -> None:
    st.subheader("Knowledge Base library")
    docs = repo.list_documents()
    if not docs:
        st.info("No documents yet. Ingest files or run: python -m research_memory.cli seed_demo")
        return
    for d in docs:
        cols = st.columns([4, 1, 1, 1])
        cols[0].markdown(
            f"**{d['filename']}**  \n"
            f"{d.get('title') or ''} · type={d.get('doc_type')} · "
            f"project={d.get('project_id') or '-'} · year={d.get('year') or '-'}"
        )
        cols[1].write(d["status"])
        cols[2].write(f"chunks={d.get('chunk_count', 0)}")
        if cols[3].button("Delete", key=f"del-{d['id']}"):
            repo.delete_document(d["id"])
            st.rerun()
        if d.get("error"):
            st.caption(f"error: {d['error']}")


def _facts_tab() -> None:
    st.subheader("Extracted Metadata / Facts")
    docs = repo.list_documents()
    if not docs:
        st.info("No facts yet.")
        return
    options = {f"{d['filename']} ({d['id'][:8]})": d["id"] for d in docs}
    choice = st.selectbox("Document", list(options.keys()))
    doc_id = options[choice]
    facts = repo.list_facts(doc_id)
    if not facts:
        st.write("No facts for this document.")
        return
    st.dataframe(
        [
            {
                "label": f["label"],
                "value": f["value"],
                "location": f["location"],
                "confidence": f["confidence"],
            }
            for f in facts
        ],
        use_container_width=True,
    )


def _chat_tab() -> None:
    st.subheader("Research Chat (citations required)")
    st.caption("Answers without Memory evidence are refused.")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("citations"):
                with st.expander("Citations"):
                    for i, c in enumerate(msg["citations"], start=1):
                        st.markdown(
                            f"**[{i}] {c['filename']} / {c['location']}** "
                            f"(score={c['score']:.3f})\n\n{c['snippet']}"
                        )

    question = st.chat_input("Ask the organizational research memory…")
    if not question:
        return

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Retrieving from Memory…"):
            result = answer_question(question, repo=repo)
        st.markdown(result.answer)
        cites = [c.to_dict() for c in result.citations]
        if cites:
            with st.expander("Citations", expanded=True):
                for i, c in enumerate(cites, start=1):
                    st.markdown(
                        f"**[{i}] {c['filename']} / {c['location']}** "
                        f"(score={c['score']:.3f})\n\n{c['snippet']}"
                    )
        st.caption(f"mode={result.mode} · refused={result.refused}")
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": result.answer,
                "citations": cites,
            }
        )


if __name__ == "__main__":
    main()
