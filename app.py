from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from html import escape
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_memory.cli import cmd_seed_demo
from research_memory.config import (
    EMBED_MODEL,
    INDEX_PATH,
    MODEL_NAME,
    MOCK_LLM,
    VECTOR_INDEX_PATH,
    ensure_data_dirs,
)
from research_memory.engine.chat import answer_question
from research_memory.engine.llm import (
    LLMConnectionError,
    generate_text,
    get_active_model,
    list_ollama_models,
    set_active_model,
)
from research_memory.engine.research_note import (
    build_research_note_docx,
    build_research_note_hwpx,
    generate_research_note_summary,
    hwpx_available,
    note_as_markdown,
    note_rows,
    parse_research_note_fields,
)
from research_memory.pipeline.extractors import extract_chunks
from research_memory.pipeline.ingest import ingest_bytes
from research_memory.engine.proposal import (
    analyze_rfp,
    build_markdown,
    export_docx_bytes,
    gather_kb_evidence,
    generate_draft,
    parse_rfp_bytes,
    suggest_roles,
)
from research_memory.engine.similarity import (
    compare_kb_documents,
    compare_upload_vs_kb,
    compare_uploads,
)
from research_memory.engine.tracking import (
    auto_link_milestones,
    gap_report,
    project_timeline,
    seed_demo_project,
)
from research_memory.kb.repository import KnowledgeRepository
from research_memory.schema import Citation

st.set_page_config(
    page_title="Research Memory Platform",
    page_icon=str(ROOT / "assets" / "favicon.svg"),
    layout="wide",
)

ensure_data_dirs()
repo = KnowledgeRepository()

PAGE_HOME = "Home"
PAGE_LIBRARY = "Library"
PAGE_CHAT = "Research Chat"
PAGE_PROPOSAL = "Proposal Intelligence"
PAGE_SIMILARITY = "Similarity Intelligence"
PAGE_MILESTONE = "Milestone Intelligence"
PAGE_RESEARCH_NOTE = "Research Note"

MAIN_NAV = [
    (PAGE_HOME, "🏠 Home"),
    (PAGE_LIBRARY, "📚 Library"),
    (PAGE_CHAT, "💬 Research Chat"),
    (PAGE_RESEARCH_NOTE, "📝 Research Note"),
]
FUTURE_NAV = [
    (PAGE_PROPOSAL, "Proposal Intelligence"),
    (PAGE_SIMILARITY, "Similarity Intelligence"),
    (PAGE_MILESTONE, "Milestone Intelligence"),
]


def _go(page: str, *, focus_upload: bool = False, doc_id: str | None = None) -> None:
    st.session_state.page = page
    if focus_upload:
        st.session_state.library_focus_upload = True
    if page == PAGE_LIBRARY:
        if doc_id:
            st.session_state.library_selected_id = doc_id
        else:
            st.session_state.pop("library_selected_id", None)
    elif doc_id:
        st.session_state.library_selected_id = doc_id
    st.rerun()


def _kb_stats() -> dict:
    docs = repo.list_documents()
    projects = repo.list_projects()
    chunk_count = sum(int(d.get("chunk_count") or 0) for d in docs)
    ready = sum(1 for d in docs if d.get("status") == "ready")
    failed = sum(1 for d in docs if d.get("status") == "failed")
    rs = repo.retrieval_status()
    if rs.get("vector_index") and rs.get("tfidf_index"):
        kb_status = "Ready (hybrid)"
    elif rs.get("vector_index"):
        kb_status = "Ready (vector)"
    elif rs.get("tfidf_index"):
        kb_status = "Ready (lexical)"
    elif docs:
        kb_status = "Needs rebuild"
    else:
        kb_status = "Empty"
    last_indexed = _last_indexed_label()
    return {
        "docs": docs,
        "doc_count": len(docs),
        "ready": ready,
        "failed": failed,
        "project_count": len(projects),
        "chunk_count": chunk_count,
        "kb_status": kb_status,
        "last_indexed": last_indexed,
        "retrieval": rs,
    }


def _last_indexed_label() -> str:
    times: list[float] = []
    for path in (VECTOR_INDEX_PATH, INDEX_PATH):
        if path.exists():
            times.append(path.stat().st_mtime)
    if not times:
        return "—"
    return datetime.fromtimestamp(max(times)).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    if "page" not in st.session_state:
        st.session_state.page = PAGE_HOME

    with st.sidebar:
        st.markdown("### Research Memory")
        st.caption("Organizational research intelligence")

        for page_id, label in MAIN_NAV:
            active = st.session_state.page == page_id
            if st.button(
                label,
                key=f"nav-{page_id}",
                use_container_width=True,
                type="primary" if active else "secondary",
            ):
                _go(page_id)

        st.markdown("---")
        st.caption("Future Capabilities")
        for page_id, label in FUTURE_NAV:
            active = st.session_state.page == page_id
            if st.button(
                label,
                key=f"nav-future-{page_id}",
                use_container_width=True,
                type="primary" if active else "secondary",
            ):
                _go(page_id)

        st.markdown("---")
        st.caption("System")
        stats = _kb_stats()
        st.write(f"Documents: **{stats['doc_count']}**")
        models = list_ollama_models()
        if models:
            if "llm_model" not in st.session_state:
                current = get_active_model()
                st.session_state.llm_model = current if current in models else models[0]
            choice = st.selectbox(
                "LLM model",
                models,
                key="llm_model",
            )
            set_active_model(choice)
            st.caption("Ollama · connected")
        else:
            st.write("LLM: **offline**")
            set_active_model(MODEL_NAME)
        st.write(f"Embed: `{EMBED_MODEL}`")
        if MOCK_LLM:
            st.warning("RM_MOCK_LLM=true")
        if st.button("Rebuild retrieval index", use_container_width=True):
            with st.spinner("Embedding chunks…"):
                n = repo.rebuild_index()
            st.success(f"chunks={n} · {repo.last_index_status}")

    page = st.session_state.page
    if page == PAGE_HOME:
        _home_page()
    elif page == PAGE_LIBRARY:
        _library_page()
    elif page == PAGE_CHAT:
        _chat_page()
    elif page == PAGE_RESEARCH_NOTE:
        st.title("Research Note")
        st.caption(
            "프로젝트 단위로 연구노트를 이어 씁니다. "
            "과거 Memory + 새 업로드 → 표 형식 노트 → 다운로드 및 Memory 저장."
        )
        _research_note_panel()
    elif page == PAGE_PROPOSAL:
        _roadmap_banner(
            "Proposal Intelligence",
            "Upload an RFP and draft the center’s contribution from Memory evidence — "
            "not full proposal auto-completion. Early access below.",
        )
        _proposal_panel()
    elif page == PAGE_SIMILARITY:
        _roadmap_banner(
            "Similarity Intelligence",
            "Compare new drafts against organizational Memory to surface reuse and overlap. "
            "Early access below.",
        )
        _similarity_panel()
    elif page == PAGE_MILESTONE:
        _roadmap_banner(
            "Milestone Intelligence",
            "Track planned deliverables against what is already stored in Memory. "
            "Early access below.",
        )
        _milestone_panel()
    else:
        _home_page()


def _roadmap_banner(title: str, blurb: str) -> None:
    st.info("**Roadmap / Coming soon** — available for early exploration")
    st.title(title)
    st.write(blurb)
    st.divider()


def _home_page() -> None:
    stats = _kb_stats()
    empty = stats["doc_count"] == 0

    if empty:
        st.title("Research Memory")
        st.subheader(
            "Build an organizational research memory that understands your documents "
            "and answers with evidence."
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("Documents", 0)
        c2.metric("Projects", 0)
        c3.metric("Knowledge Chunks", 0)

        a1, a2 = st.columns(2)
        if a1.button("+ Upload", type="primary", use_container_width=True):
            _go(PAGE_LIBRARY, focus_upload=True)
        if a2.button("Load Demo Dataset", use_container_width=True):
            with st.spinner("Loading demo documents into Memory…"):
                cmd_seed_demo(argparse.Namespace())
            st.success("Demo dataset loaded.")
            st.rerun()

        st.markdown("---")
        st.markdown("### How It Works")
        st.markdown(
            """
1. **Upload Documents** — add proposals, notes, papers, and meeting records  
↓  
2. **Explore Library** — browse what the center has done  
↓  
3. **Ask with Evidence** — Research Chat answers only with citations
"""
        )
        return

    st.title("Research Memory")
    st.caption("Organizational research dashboard")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Documents", stats["doc_count"])
    c2.metric("Projects", stats["project_count"])
    c3.metric("Chunks", stats["chunk_count"])
    c4.metric("Last Updated", stats["last_indexed"])

    st.markdown("#### Knowledge Base")
    m1, m2 = st.columns(2)
    m1.write(f"**Status:** {stats['kb_status']}")
    m2.write(f"**Embedding:** `{EMBED_MODEL}`")

    st.markdown("#### Quick Actions")
    q1, q2, q3 = st.columns(3)
    if q1.button("+ Upload", type="primary", use_container_width=True):
        _go(PAGE_LIBRARY, focus_upload=True)
    if q2.button("+ Library", use_container_width=True):
        _go(PAGE_LIBRARY)
    if q3.button("+ Chat", use_container_width=True):
        _go(PAGE_CHAT)

    st.markdown("#### Recent Documents")
    st.caption("Open a document to explore Memory — then ask in Chat with context.")
    recent = stats["docs"][:8]
    for d in recent:
        label = d.get("title") or d["filename"]
        meta = (
            f"{d.get('doc_type') or 'doc'} · "
            f"{d.get('project_id') or 'no project'} · "
            f"{str(d.get('created_at') or '')[:10]} · "
            f"chunks={d.get('chunk_count', 0)}"
        )
        cols = st.columns([5, 1])
        cols[0].markdown(f"**{label}**  \n{meta}")
        if cols[1].button("Open", key=f"home-open-{d['id']}"):
            _go(PAGE_LIBRARY, doc_id=d["id"])


def _library_page() -> None:
    st.title("Library")
    st.caption("Browse research by project — then upload or search documents.")

    docs = repo.list_documents()
    focus_upload = bool(st.session_state.pop("library_focus_upload", False))

    if not docs:
        st.info("Memory is empty. Upload documents below, or load the demo dataset from Home.")
        with st.expander("Upload Documents", expanded=True):
            _upload_panel()
        return

    selected_id = st.session_state.get("library_selected_id")
    if selected_id and any(d["id"] == selected_id for d in docs):
        _document_detail(selected_id, docs)
        return

    if "library_view" not in st.session_state:
        st.session_state.library_view = "projects"

    view = st.session_state.library_view

    if view == "search":
        if st.button("← Projects", key="lib_to_projects"):
            st.session_state.library_view = "projects"
            st.session_state.pop("library_project_focus", None)
            st.rerun()
        _library_search_view(docs)
        return

    if view == "project_docs":
        if st.button("← Projects", key="lib_to_projects_from_docs"):
            st.session_state.library_view = "projects"
            st.session_state.pop("library_project_focus", None)
            st.rerun()
        pid = st.session_state.get("library_project_focus") or ""
        _library_project_docs_view(docs, pid)
        return

    # Default: Projects on top, then Upload + Search
    _library_projects_view(docs)

    st.markdown("---")
    st.markdown("### Upload")
    with st.expander("Upload Documents", expanded=focus_upload):
        _upload_panel()

    st.markdown("### Search")
    if st.button("Search documents", type="primary", use_container_width=True, key="lib_to_search"):
        st.session_state.library_view = "search"
        st.rerun()
    st.caption("Search and filter across all documents in Memory.")


def _library_projects_view(docs: list) -> None:
    st.markdown("### Projects")
    st.caption("Open a project to see its documents in Memory.")

    groups = _group_docs_by_project(docs)
    items = sorted(
        groups.items(),
        key=lambda kv: (-len(kv[1]), kv[0]),
    )

    # 3-column compact card grid
    for i in range(0, len(items), 3):
        cols = st.columns(3)
        for j, col in enumerate(cols):
            if i + j >= len(items):
                break
            pid, group = items[i + j]
            with col:
                _project_card(pid, group)


def _project_card(project_id: str, docs: list) -> None:
    years = sorted({str(d.get("year")) for d in docs if d.get("year") is not None})
    latest = max((str(d.get("created_at") or "") for d in docs), default="")[:10]
    year_s = "–".join([years[0], years[-1]]) if len(years) > 1 else (years[0] if years else "—")

    note_n = sum(
        1
        for d in docs
        if (d.get("doc_type") or "").lower() in {"note", "research_note"}
        or str(d.get("filename") or "").startswith("research_note_")
        or "연구노트" in str(d.get("title") or "")
    )
    doc_n = len(docs)

    st.markdown(
        f"""
<div style="
  border:1px solid #d9dde3; border-radius:10px; padding:12px 12px 8px;
  background:#fff; margin-bottom:4px;">
  <div style="font-size:16px;font-weight:700;margin:0 0 8px;color:#111827;">{_html_esc(project_id)}</div>
  <div style="font-size:13px;color:#374151;line-height:1.55;">
    자료 <b>{doc_n}</b>건 | 연구노트 <b>{note_n}</b>개<br/>
    years {_html_esc(year_s)}<br/>
    updated {_html_esc(latest or '—')}
  </div>
</div>
""",
        unsafe_allow_html=True,
    )
    b1, b2 = st.columns(2)
    if b1.button("Open", key=f"proj-open-{project_id}", use_container_width=True, type="primary"):
        st.session_state.library_view = "project_docs"
        st.session_state.library_project_focus = project_id
        st.rerun()
    if b2.button("Search", key=f"proj-search-{project_id}", use_container_width=True):
        st.session_state.library_view = "search"
        st.session_state.lib_project = project_id
        st.rerun()


def _html_esc(text: str) -> str:
    return escape(str(text or ""))


def _group_docs_by_project(docs: list) -> dict[str, list]:
    groups: dict[str, list] = {}
    for d in docs:
        pid = (d.get("project_id") or "").strip() or "(No project)"
        groups.setdefault(pid, []).append(d)
    return groups


def _library_project_docs_view(docs: list, project_id: str) -> None:
    if not project_id:
        st.session_state.library_view = "projects"
        st.rerun()
        return

    st.markdown(f"### {project_id}")
    if project_id == "(No project)":
        group = [d for d in docs if not (d.get("project_id") or "").strip()]
    else:
        group = [d for d in docs if (d.get("project_id") or "").strip() == project_id]

    st.caption(f"{len(group)} documents in this project")
    group = _sort_documents(group, key_prefix=f"proj-{project_id}")
    _library_doc_list(group, key_prefix=f"proj-{project_id}")


def _library_search_view(docs: list) -> None:
    st.markdown("### Search")
    q = st.text_input(
        "Search Memory",
        placeholder="Search by title, filename, project, type…",
        key="lib_search",
    )

    types = sorted({(d.get("doc_type") or "").strip() for d in docs if d.get("doc_type")})
    projects = sorted(
        {(d.get("project_id") or "").strip() for d in docs if d.get("project_id")}
    )
    years = sorted(
        {str(d.get("year")) for d in docs if d.get("year") is not None},
        reverse=True,
    )
    statuses = sorted({(d.get("status") or "").strip() for d in docs if d.get("status")})

    f1, f2, f3, f4, f5 = st.columns([1.3, 1.3, 1.0, 1.0, 2.2])
    type_f = f1.selectbox("Type", ["All"] + types, key="lib_type")
    proj_options = ["All"] + projects
    if st.session_state.get("lib_project") not in proj_options:
        st.session_state.lib_project = "All"
    project_f = f2.selectbox("Project", proj_options, key="lib_project")
    year_f = f3.selectbox("Year", ["All"] + years, key="lib_year")
    status_f = f4.selectbox("Status", ["All"] + statuses, key="lib_status")

    with f5:
        _render_sort_controls(
            field_key="search_sort_field",
            dir_key="search_sort_desc",
            btn_key="search_sort_dir_btn",
        )

    filtered = _filter_documents(
        docs,
        query=q,
        doc_type=None if type_f == "All" else type_f,
        project_id=None if project_f == "All" else project_f,
        year=None if year_f == "All" else year_f,
        status=None if status_f == "All" else status_f,
    )

    st.caption(f"{len(filtered)} results")
    if not filtered:
        st.warning("No documents match these filters.")
        return

    filtered = _apply_sort(
        filtered,
        field=st.session_state.search_sort_field,
        descending=bool(st.session_state.search_sort_desc),
    )
    _library_doc_list(filtered, key_prefix="search")


def _render_sort_controls(*, field_key: str, dir_key: str, btn_key: str) -> None:
    """Sort-by select + tiny ▲/▼ on one visual row (aligned with selectbox input)."""
    if field_key not in st.session_state:
        st.session_state[field_key] = "Date"
    if dir_key not in st.session_state:
        st.session_state[dir_key] = True

    left, right = st.columns([5, 1])
    left.selectbox("Sort by", ["Date", "Title", "Type", "Project", "Year"], key=field_key)
    # push button down to the selectbox input (below the label)
    right.markdown(
        "<div style='height:29px;line-height:29px;font-size:12px;color:transparent;'>.</div>",
        unsafe_allow_html=True,
    )
    desc = bool(st.session_state[dir_key])
    if right.button(
        "▼" if desc else "▲",
        key=btn_key,
        help="▼ newest/Z→A · ▲ oldest/A→Z",
    ):
        st.session_state[dir_key] = not desc
        st.rerun()


def _sort_documents(docs: list, *, key_prefix: str) -> list:
    """Sort controls for project doc lists."""
    field_key = f"{key_prefix}_sort_field"
    dir_key = f"{key_prefix}_sort_desc"
    btn_key = f"{key_prefix}_sort_dir_btn"
    _render_sort_controls(field_key=field_key, dir_key=dir_key, btn_key=btn_key)
    return _apply_sort(
        docs,
        field=st.session_state[field_key],
        descending=bool(st.session_state[dir_key]),
    )


def _apply_sort(docs: list, *, field: str, descending: bool) -> list:
    def sort_key(d: dict):
        if field == "Date":
            return str(d.get("created_at") or "")
        if field == "Title":
            return (d.get("title") or d.get("filename") or "").lower()
        if field == "Type":
            return (d.get("doc_type") or "").lower()
        if field == "Project":
            return (d.get("project_id") or "").lower()
        if field == "Year":
            y = d.get("year")
            return int(y) if y is not None else -1
        return ""

    return sorted(docs, key=sort_key, reverse=descending)


def _library_doc_list(docs: list, *, key_prefix: str) -> None:
    """Inline openable document rows — no separate Open dropdown."""
    for d in docs:
        title = d.get("title") or d["filename"]
        meta = (
            f"`{d['filename']}` · {d.get('doc_type') or '—'} · "
            f"{d.get('project_id') or '—'} · {d.get('year') or '—'} · "
            f"{d.get('status')} · {str(d.get('created_at') or '')[:10]}"
        )
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"**{title}**  \n{meta}")
        if c2.button("Open", key=f"{key_prefix}-open-{d['id']}", use_container_width=True):
            st.session_state.library_selected_id = d["id"]
            st.rerun()


def _filter_documents(
    docs: list,
    *,
    query: str,
    doc_type: str | None,
    project_id: str | None,
    year: str | None,
    status: str | None,
) -> list:
    q = (query or "").strip().lower()
    out = []
    for d in docs:
        if doc_type and (d.get("doc_type") or "") != doc_type:
            continue
        if project_id and (d.get("project_id") or "") != project_id:
            continue
        if year and str(d.get("year") or "") != year:
            continue
        if status and (d.get("status") or "") != status:
            continue
        if q:
            blob = " ".join(
                [
                    str(d.get("title") or ""),
                    str(d.get("filename") or ""),
                    str(d.get("project_id") or ""),
                    str(d.get("doc_type") or ""),
                    str(d.get("full_text") or "")[:2000],
                ]
            ).lower()
            if q not in blob:
                continue
        out.append(d)
    return out


def _document_detail(doc_id: str, all_docs: list) -> None:
    doc = repo.get_document(doc_id) or next((d for d in all_docs if d["id"] == doc_id), None)
    if not doc:
        st.session_state.pop("library_selected_id", None)
        st.warning("Document not found.")
        return

    top = st.columns([1, 2, 1])
    if top[0].button("← Back", use_container_width=True):
        st.session_state.pop("library_selected_id", None)
        if not st.session_state.get("library_view"):
            st.session_state.library_view = "projects"
        st.rerun()
    if top[1].button("Ask about this in Chat", use_container_width=True):
        title = doc.get("title") or doc["filename"]
        st.session_state.chat_prefill = (
            f"{title} 문서 기준으로, Memory 근거를 들어 핵심 내용을 요약해 주세요."
        )
        _go(PAGE_CHAT)
    if top[2].button("Delete", use_container_width=True, key=f"del-top-{doc_id}"):
        st.session_state[f"confirm_del_{doc_id}"] = True

    if st.session_state.get(f"confirm_del_{doc_id}"):
        st.warning(f"Delete `{doc.get('filename')}` from Memory?")
        c_yes, c_no = st.columns(2)
        if c_yes.button("Yes, delete", type="primary", key=f"del-yes-{doc_id}"):
            repo.delete_document(doc_id)
            st.session_state.pop("library_selected_id", None)
            st.session_state.pop(f"confirm_del_{doc_id}", None)
            st.session_state.pop(f"ai_summary_{doc_id}", None)
            st.rerun()
        if c_no.button("Cancel", key=f"del-no-{doc_id}"):
            st.session_state.pop(f"confirm_del_{doc_id}", None)
            st.rerun()

    title = doc.get("title") or doc["filename"]
    st.markdown(f"## {title}")
    st.caption(
        f"`{doc['filename']}` · {doc.get('doc_type') or '—'} · "
        f"{doc.get('project_id') or '—'} · {doc.get('year') or '—'} · "
        f"{doc.get('status')}"
    )

    tab_preview, tab_sum, tab_related = st.tabs(["Preview", "Summary", "Related"])

    with tab_preview:
        edit_key = f"lib_edit_mode_{doc_id}"
        if edit_key not in st.session_state:
            st.session_state[edit_key] = False

        e1, e2 = st.columns([1, 4])
        if e1.button(
            "Done" if st.session_state[edit_key] else "Edit",
            key=f"lib-edit-toggle-{doc_id}",
            use_container_width=True,
        ):
            st.session_state[edit_key] = not st.session_state[edit_key]
            st.rerun()

        if st.session_state[edit_key]:
            new_title = st.text_input(
                "Title",
                value=doc.get("title") or "",
                key=f"lib-edit-title-{doc_id}",
            )
            new_project = st.text_input(
                "Project ID",
                value=doc.get("project_id") or "",
                key=f"lib-edit-project-{doc_id}",
            )
            new_text = st.text_area(
                "Body",
                value=doc.get("full_text") or "",
                height=420,
                key=f"lib-edit-body-{doc_id}",
            )
            if st.button("Save changes", type="primary", key=f"lib-edit-save-{doc_id}"):
                with st.spinner("Saving and re-indexing…"):
                    repo.update_document(
                        doc_id,
                        title=new_title.strip(),
                        project_id=new_project.strip(),
                        full_text=new_text,
                    )
                st.session_state[edit_key] = False
                st.session_state.pop(f"ai_summary_{doc_id}", None)
                st.success("Saved.")
                st.rerun()
        else:
            text = (doc.get("full_text") or "").strip()
            if not text:
                st.caption("미리볼 텍스트가 없습니다.")
            else:
                st.text(text)
                st.caption(f"{len(text):,} characters")

    with tab_sum:
        st.caption("원문은 Preview에서 보세요.")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Project", doc.get("project_id") or "—")
        m2.metric("Type", doc.get("doc_type") or "—")
        m3.metric("Year", str(doc.get("year") or "—"))
        m4.metric("Chunks", int(doc.get("chunk_count") or 0))

        cache_key = f"ai_summary_{doc_id}"
        st.markdown("#### 요약")
        if st.session_state.get(cache_key):
            st.write(st.session_state[cache_key])
        else:
            st.write(_document_snapshot(doc))

        if st.button("AI로 다시 요약", key=f"sum-{doc_id}"):
            with st.spinner("요약 생성 중…"):
                st.session_state[cache_key] = _ai_summary(doc)
            st.rerun()

    with tab_related:
        related = _related_documents(doc, all_docs)
        if not related:
            st.caption("관련된 문서를 아직 찾지 못했습니다.")
        else:
            st.caption("같은 과제 · 비슷한 키워드 기준")
            for r, reason in related:
                cols = st.columns([4, 1])
                cols[0].markdown(
                    f"**{r.get('title') or r['filename']}**  \n"
                    f"{reason} · {r.get('project_id') or '—'} · "
                    f"{str(r.get('created_at') or '')[:10]}"
                )
                if cols[1].button("Open", key=f"rel-{r['id']}"):
                    st.session_state.library_selected_id = r["id"]
                    st.rerun()


def _document_snapshot(doc: dict) -> str:
    """Short human digest for Summary — not a Preview substitute."""
    text = (doc.get("full_text") or "").strip()
    if not text:
        return "No text available."
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # skip repeated meta lines already shown in header metrics
        low = line.lower()
        if low.startswith(("과제명", "작성자", "연도", "문서유형", "project:", "author")):
            continue
        if line.startswith("Project:"):
            continue
        lines.append(line)
        if len(" ".join(lines)) >= 280:
            break
    if not lines:
        # fall back to first non-heading paragraph
        for raw in text.splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                lines.append(line)
                break
    snap = " ".join(lines)
    if len(snap) > 320:
        snap = snap[:317] + "…"
    return snap or "No snapshot available."


def _ai_summary(doc: dict) -> str:
    text = (doc.get("full_text") or "")[:3500]
    if not text.strip():
        return "No text available to summarize."
    prompt = (
        "Summarize this organizational research document in 4-6 short Korean bullets. "
        "Focus on purpose, method/role, and outcomes. Do not invent facts.\n\n"
        f"Title: {doc.get('title') or doc.get('filename')}\n"
        f"Project: {doc.get('project_id') or '-'}\n\n"
        f"Text:\n{text}"
    )
    try:
        return generate_text(prompt)
    except LLMConnectionError as exc:
        return (
            f"AI summary unavailable ({exc}). "
            f"Snapshot: {_document_snapshot(doc)}"
        )


def _related_documents(doc: dict, all_docs: list, limit: int = 6) -> list[tuple[dict, str]]:
    pid = (doc.get("project_id") or "").strip()
    tokens = _tokens(
        " ".join(
            [
                str(doc.get("title") or ""),
                str(doc.get("filename") or ""),
                str(doc.get("doc_type") or ""),
            ]
        )
    )
    scored: list[tuple[float, dict, str]] = []
    for other in all_docs:
        if other["id"] == doc["id"]:
            continue
        score = 0.0
        reasons = []
        other_pid = (other.get("project_id") or "").strip()
        if pid and other_pid and pid == other_pid:
            score += 3.0
            reasons.append(f"same project ({pid})")
        other_tokens = _tokens(
            " ".join(
                [
                    str(other.get("title") or ""),
                    str(other.get("filename") or ""),
                    str(other.get("doc_type") or ""),
                ]
            )
        )
        overlap = tokens & other_tokens
        if overlap:
            score += min(2.0, 0.4 * len(overlap))
            reasons.append("keywords: " + ", ".join(sorted(overlap)[:4]))
        if score <= 0:
            continue
        scored.append((score, other, " · ".join(reasons) if reasons else "related"))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [(d, reason) for _, d, reason in scored[:limit]]


def _tokens(text: str) -> set[str]:
    raw = "".join(ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in text)
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "md",
        "pdf",
        "docx",
        "txt",
        "demo",
        "문서",
    }
    return {t for t in raw.split() if len(t) >= 3 and t not in stop}


def _upload_panel() -> None:
    project_id = st.text_input(
        "Project ID (optional)",
        placeholder="e.g. KETI-2026-001",
        key="lib_project_id",
    )
    uploads = st.file_uploader(
        "Select files",
        type=["pdf", "docx", "txt", "md", "csv", "xlsx", "xls", "hwpx"],
        accept_multiple_files=True,
        key="lib_uploads",
    )
    if st.button("Upload into Memory", type="primary", disabled=not uploads, key="lib_ingest"):
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
                    st.info(f"{f.name}: already in Memory")
                else:
                    st.success(
                        f"{f.name}: chunks={result.get('chunks')} facts={result.get('facts')}"
                    )
                    with st.expander(f"Metadata — {f.name}"):
                        st.json(result.get("metadata") or {})
            else:
                st.error(f"{f.name}: {result.get('error', 'failed')}")
        st.rerun()


def _chat_page() -> None:
    st.title("Research Chat")
    st.caption("답변 후, 근거(Evidence)는 펼쳐서 확인합니다. 근거 없으면 답하지 않습니다.")

    docs = [d for d in repo.list_documents() if d.get("status") == "ready"]
    if not docs:
        st.warning("Your Knowledge Base is empty.")
        st.write(
            "Explore Library after uploading documents, then come back to ask with evidence."
        )
        c1, c2 = st.columns(2)
        if c1.button("Go to Library", type="primary", use_container_width=True):
            _go(PAGE_LIBRARY, focus_upload=True)
        if c2.button("Go to Home", use_container_width=True):
            _go(PAGE_HOME)
        return

    if "messages" not in st.session_state:
        st.session_state.messages = []

    prefill = st.session_state.get("chat_prefill")
    if prefill:
        st.info(f"From Library: {prefill}")
        if st.button("Ask this question", type="primary"):
            st.session_state.pop("chat_prefill", None)
            st.session_state["_pending_question"] = prefill
            st.rerun()

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("citations"):
                with st.expander("Evidence", expanded=False):
                    _render_evidence(msg["citations"])

    question = st.session_state.pop("_pending_question", None) or st.chat_input(
        "Ask the organizational research memory…"
    )
    if not question:
        return

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Memory에서 근거를 찾는 중…"):
            result = answer_question(question, repo=repo)
        cites = [c.to_dict() for c in result.citations]
        st.markdown(result.answer)
        if cites:
            with st.expander("Evidence", expanded=False):
                _render_evidence(cites)
        st.caption(f"mode={result.mode} · refused={result.refused}")
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": result.answer,
                "citations": cites,
            }
        )


def _render_evidence(cites: list[dict]) -> None:
    for i, c in enumerate(cites[:5], start=1):
        st.markdown(
            f"**{i}. `{c['filename']}`** · {c.get('location') or '—'} · "
            f"score={float(c.get('score') or 0):.3f}  \n"
            f"{c.get('snippet') or ''}"
        )
        if i < min(5, len(cites)):
            st.divider()


def _rn_project_choices() -> list[str]:
    ids: set[str] = set()
    for p in repo.list_projects():
        pid = (p.get("project_id") or "").strip()
        if pid:
            ids.add(pid)
    for d in repo.list_documents():
        pid = (d.get("project_id") or "").strip()
        if pid:
            ids.add(pid)
    return sorted(ids)


def _rn_extract_upload_text(uploads) -> tuple[list[str], list[str]]:
    """Return (text parts, filenames) from Streamlit UploadedFile list."""
    parts: list[str] = []
    names: list[str] = []
    if not uploads:
        return parts, names
    for f in uploads:
        names.append(f.name)
        tmp = ROOT / "data" / "raw" / f"_rn_tmp_{f.name}"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(f.getvalue())
        try:
            _ftype, chunks, err = extract_chunks(tmp)
            if err:
                parts.append(f"# {f.name}\n[extract error: {err}]")
            else:
                body = "\n".join(c.text for c in chunks if c.text).strip()
                parts.append(f"# {f.name}\n{body[:6000]}" if body else f"# {f.name}\n")
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
    return parts, names


def _research_note_panel() -> None:
    """Project-scoped research note: past Memory + new uploads → table note → save."""
    st.subheader("연구노트 작성")
    st.caption(
        "프로젝트에 묶어 이어 쓰고, 과거 Memory + 새 자료를 합쳐 표 형식 연구노트로 저장합니다."
    )

    # defaults
    for k, v in (
        ("rn_topic", ""),
        ("rn_owner", ""),
        ("rn_author", ""),
        ("rn_content", ""),
        ("rn_results", ""),
        ("rn_etc", ""),
        ("rn_writer_text", ""),
    ):
        if k not in st.session_state:
            st.session_state[k] = v
    if "rn_date" not in st.session_state:
        st.session_state.rn_date = date.today()

    # pending convert (widget-safe)
    pending = st.session_state.pop("rn_pending_content", None)
    if pending is not None:
        text = str(pending)
        parsed = parse_research_note_fields(text)
        if parsed.get("topic") or parsed.get("results") or "주제 제안" in text:
            if parsed.get("topic"):
                st.session_state.rn_topic = parsed["topic"]
            if str(parsed.get("content") or "").strip():
                st.session_state.rn_content = parsed["content"]
            if parsed.get("results"):
                st.session_state.rn_results = parsed["results"]
        else:
            st.session_state.rn_content = text
        st.session_state.rn_converted = True

    st.markdown("#### 1. Project")
    projects = _rn_project_choices()
    pcol1, pcol2 = st.columns([2, 2])
    with pcol1:
        choice = st.selectbox(
            "Library 프로젝트 선택",
            ["(선택)"] + projects,
            key="rn_project_select",
        )
    with pcol2:
        custom = st.text_input(
            "또는 새 Project ID",
            key="rn_project_custom",
            placeholder="e.g. ALD-2024",
        )
    project_id = (custom or "").strip() or (
        "" if choice == "(선택)" else choice
    )
    if not project_id:
        st.info("먼저 이 연구노트가 속할 **Project**를 선택하거나 입력하세요.")
        return
    st.session_state.rn_project_id = project_id
    st.caption(f"현재 프로젝트: `{project_id}`")

    st.markdown("#### 2. Sources")
    scol1, scol2 = st.columns(2)
    with scol1:
        st.markdown("**Connecting to past Memory**")
        proj_docs = [
            d
            for d in repo.list_documents()
            if d.get("status") == "ready"
            and (d.get("project_id") or "").strip() == project_id
        ]
        # also allow browsing other memory docs
        other = [
            d
            for d in repo.list_documents()
            if d.get("status") == "ready"
            and (d.get("project_id") or "").strip() != project_id
        ]
        mem_labels = {
            f"[this project] {d.get('title') or d['filename']}": d["id"] for d in proj_docs
        }
        mem_labels.update(
            {
                f"[{d.get('project_id') or '—'}] {d.get('title') or d['filename']}": d["id"]
                for d in other
            }
        )
        picked = st.multiselect(
            "과거 Memory 문서",
            list(mem_labels.keys()),
            key="rn_mem_docs",
            help="같은 과제에서 이어갈 과거 노트·보고서 등",
        )
        if not proj_docs:
            st.caption("이 프로젝트에 아직 Memory 문서가 없습니다.")

    with scol2:
        st.markdown("**New / updated files**")
        uploads = st.file_uploader(
            "새 자료 업로드",
            type=["pdf", "docx", "txt", "md", "csv", "xlsx", "xls", "hwpx"],
            accept_multiple_files=True,
            key="rn_uploads",
            help="Document Analyser처럼 이번에 추가된 자료",
        )
        paste = st.text_area(
            "짧은 메모 / 붙여넣기",
            height=100,
            key="rn_paste",
            placeholder="오늘 진행 메모…",
        )

    if st.button("연구노트용 통합 요약 생성", type="primary", key="rn_gen"):
        parts: list[str] = []
        filenames: list[str] = []
        for lab in picked:
            doc = repo.get_document(mem_labels[lab])
            if not doc:
                continue
            filenames.append(doc.get("filename") or lab)
            body = (doc.get("full_text") or "").strip()
            if body:
                parts.append(f"# [Memory] {doc.get('filename')}\n{body[:4000]}")
        up_parts, up_names = _rn_extract_upload_text(uploads)
        parts.extend(up_parts)
        filenames.extend(up_names)
        if paste.strip():
            parts.append(f"# note\n{paste.strip()}")
            filenames.append("pasted_note")
        if not parts:
            st.warning("과거 Memory 문서나 새 파일을 하나 이상 넣어 주세요.")
        else:
            with st.spinner("연구노트용 요약 생성 중…"):
                summary = generate_research_note_summary(
                    "\n\n".join(parts),
                    filenames=filenames,
                )
            st.session_state.rn_writer_text = summary
            st.session_state.rn_source_files = filenames
            parsed = parse_research_note_fields(summary)
            if parsed.get("topic"):
                st.session_state.rn_topic = parsed["topic"]
            if parsed.get("content"):
                st.session_state.rn_content = parsed["content"]
            if parsed.get("results"):
                st.session_state.rn_results = parsed["results"]
            # auto-convert into table form (preview = download)
            st.session_state.rn_converted = True
            st.rerun()

    summary = st.session_state.get("rn_writer_text") or ""
    if not summary and not any(
        str(st.session_state.get(k) or "").strip()
        for k in ("rn_topic", "rn_content", "rn_results")
    ):
        st.info(
            "1. Project 선택\n"
            "2. 과거 Memory + 새 파일/메모\n"
            "3. **통합 요약 생성** → 표 편집 → 다운로드 / Memory 저장"
        )
        return

    files = st.session_state.get("rn_source_files") or []
    if files:
        st.caption(f"참고 파일: {', '.join(files)}")

    st.markdown("#### 3. Draft")
    left, mid, right = st.columns([5, 1.2, 5], gap="medium")
    with left:
        st.markdown("**요약문**")
        st.text_area(
            "요약문 편집",
            key="rn_writer_text",
            height=280,
            label_visibility="collapsed",
        )
        st.markdown("**연구노트 미리보기 (표)**")
        components.html(_rn_preview_html(), height=420, scrolling=True)

    with mid:
        st.write("")
        st.write("")
        st.write("")
        if st.button("표로 반영", type="primary", use_container_width=True, key="rn_convert"):
            st.session_state.rn_pending_content = st.session_state.get("rn_writer_text") or ""
            st.rerun()
        st.caption("요약 → 표")
        if st.session_state.get("rn_converted"):
            st.caption("반영됨 →")

    with right:
        st.markdown("**연구노트 표**")
        st.text_input("주제", key="rn_topic")
        st.text_input("책임자", key="rn_owner")
        st.date_input("일시", key="rn_date")
        st.text_input("작성자", key="rn_author")
        st.text_area("내용", key="rn_content", height=160)
        st.text_area("연구결과", key="rn_results", height=100)
        st.text_area("기타내용", key="rn_etc", height=80)

    st.markdown("---")
    st.subheader("4. Export & Save")
    d = st.session_state.get("rn_date")
    date_s = d.isoformat() if hasattr(d, "isoformat") else str(d or "")
    rows = note_rows(
        topic=st.session_state.get("rn_topic") or "",
        owner=st.session_state.get("rn_owner") or "",
        date_s=date_s,
        author=st.session_state.get("rn_author") or "",
        content=st.session_state.get("rn_content") or "",
        results=st.session_state.get("rn_results") or "",
        etc=st.session_state.get("rn_etc") or "",
    )

    # Always export table format (same as preview)
    try:
        docx_bytes = build_research_note_docx(rows)
        hwpx_bytes = None
        hwpx_err = ""
        if hwpx_available():
            try:
                hwpx_bytes = build_research_note_hwpx(rows)
            except Exception as exc:  # noqa: BLE001
                hwpx_err = str(exc)
    except Exception as exc:  # noqa: BLE001
        st.error(f"표 형식 파일 생성 실패: {exc}")
        return

    topic_slug = re.sub(r"[^\w가-힣\-]+", "_", (st.session_state.get("rn_topic") or "research_note"))[:40]
    base_name = f"research_note_{project_id}_{date_s}_{topic_slug}".strip("_")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button(
            "DOCX (표)",
            data=docx_bytes,
            file_name=f"{base_name}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            use_container_width=True,
            key="rn_dl_docx",
            type="primary",
        )
    with c2:
        st.download_button(
            "HWPX (표)",
            data=hwpx_bytes or b"",
            file_name=f"{base_name}.hwpx",
            mime="application/hwp+zip",
            use_container_width=True,
            key="rn_dl_hwpx",
            disabled=not bool(hwpx_bytes),
        )
    with c3:
        if st.button("Memory에 저장", use_container_width=True, key="rn_save_mem"):
            md = note_as_markdown(
                topic=st.session_state.get("rn_topic") or "",
                owner=st.session_state.get("rn_owner") or "",
                date_s=date_s,
                author=st.session_state.get("rn_author") or "",
                content=st.session_state.get("rn_content") or "",
                results=st.session_state.get("rn_results") or "",
                etc=st.session_state.get("rn_etc") or "",
                project_id=project_id,
            )
            with st.spinner("Memory에 연구노트 저장 중…"):
                result = ingest_bytes(
                    md.encode("utf-8"),
                    f"{base_name}.md",
                    repo=repo,
                    project_id=project_id,
                )
                # also keep table DOCX in Memory
                ingest_bytes(
                    docx_bytes,
                    f"{base_name}.docx",
                    repo=repo,
                    project_id=project_id,
                )
            if result.get("ok"):
                st.success(
                    f"저장됨 · project=`{project_id}` · "
                    f"{'updated' if result.get('skipped') else 'new'} "
                    f"{result.get('filename')}"
                )
            else:
                st.error(result.get("error") or "저장 실패")

    if hwpx_err:
        st.caption(f"HWPX 생성 불가 — DOCX/Memory 저장은 가능합니다. ({hwpx_err[:160]})")
    st.caption("다운로드·저장은 미리보기와 같은 **표 형식**입니다.")


def _rn_preview_html() -> str:
    d = st.session_state.get("rn_date")
    date_s = d.isoformat() if hasattr(d, "isoformat") else str(d or "")
    rows = note_rows(
        topic=st.session_state.get("rn_topic") or "",
        owner=st.session_state.get("rn_owner") or "",
        date_s=date_s,
        author=st.session_state.get("rn_author") or "",
        content=st.session_state.get("rn_content") or "",
        results=st.session_state.get("rn_results") or "",
        etc=st.session_state.get("rn_etc") or "",
    )
    tall = {"내 용", "연구결과", "기타내용"}
    body = []
    for label, val in rows:
        min_h = "140px" if label == "내 용" else ("90px" if label in tall else "36px")
        body.append(
            f"""<tr>
  <th style="width:22%;background:#f0f0f0;text-align:center;vertical-align:middle;
             border:1px solid #333;padding:8px;font-weight:600;">{escape(label)}</th>
  <td style="border:1px solid #333;padding:8px;vertical-align:top;min-height:{min_h};
             white-space:pre-wrap;">{escape(val)}</td>
</tr>"""
        )
    return f"""
<div style="font-family:'Malgun Gothic','맑은 고딕',sans-serif;color:#111;">
  <div style="text-align:center;font-size:22px;font-weight:700;margin:8px 0 14px;">연구노트</div>
  <table style="width:100%;border-collapse:collapse;table-layout:fixed;">
    {''.join(body)}
  </table>
</div>
"""


def _similarity_panel() -> None:
    st.subheader("Compare documents")
    st.caption(
        "Compare a new draft against organizational Memory, two KB docs, "
        "or uploaded files. Exact + TF-IDF similar sentence pairs."
    )
    mode = st.radio(
        "Mode",
        ["Upload vs Knowledge Base", "KB document vs KB document", "Uploads vs Uploads"],
        horizontal=True,
    )
    threshold = st.slider("Similarity threshold", 0.5, 0.95, 0.72, 0.01)

    if mode == "Upload vs Knowledge Base":
        project_filter = st.text_input(
            "Limit KB to Project ID (optional)",
            key="sim_project",
            placeholder="e.g. DEMO-2026",
        )
        upload = st.file_uploader(
            "New document to check",
            type=["pdf", "docx", "txt", "md", "csv", "xlsx", "hwpx"],
            key="sim_upload_kb",
        )
        if st.button("Run Similarity", type="primary", key="sim_run_kb", disabled=not upload):
            with st.spinner("Comparing against Memory…"):
                result = compare_upload_vs_kb(
                    upload.getvalue(),
                    upload.name,
                    repo=repo,
                    threshold=threshold,
                    project_id=project_filter.strip() or None,
                )
            _render_similarity_result(result)

    elif mode == "KB document vs KB document":
        docs = [d for d in repo.list_documents() if d.get("status") == "ready"]
        if len(docs) < 2:
            st.info("Need at least 2 ready documents in the Knowledge Base.")
            return
        labels = {f"{d['filename']} ({d['id'][:8]})": d["id"] for d in docs}
        c1, c2 = st.columns(2)
        a = c1.selectbox("Document A", list(labels.keys()), key="sim_doc_a")
        b = c2.selectbox("Document B", list(labels.keys()), key="sim_doc_b")
        if st.button("Run Similarity", type="primary", key="sim_run_pair"):
            if labels[a] == labels[b]:
                st.warning("Pick two different documents.")
            else:
                with st.spinner("Comparing KB documents…"):
                    result = compare_kb_documents(
                        labels[a],
                        labels[b],
                        repo=repo,
                        threshold=threshold,
                    )
                _render_similarity_result(result)

    else:
        uploads = st.file_uploader(
            "Upload 2+ documents",
            type=["pdf", "docx", "txt", "md", "csv", "xlsx", "hwpx"],
            accept_multiple_files=True,
            key="sim_uploads",
        )
        if st.button(
            "Run Similarity",
            type="primary",
            key="sim_run_uploads",
            disabled=not uploads or len(uploads) < 2,
        ):
            with st.spinner("Comparing uploads…"):
                result = compare_uploads(
                    [(f.getvalue(), f.name) for f in uploads],
                    threshold=threshold,
                )
            _render_similarity_result(result)


def _render_similarity_result(result: dict) -> None:
    if not result.get("ok"):
        st.error(result.get("error") or "Similarity failed")
        return
    stats = result.get("stats") or {}
    st.success(
        f"pairs={stats.get('pair_count', 0)} · exact={stats.get('exact', 0)} · "
        f"high={stats.get('high', 0)} · medium={stats.get('medium', 0)} · "
        f"max_score={stats.get('max_score', 0):.3f}"
    )
    meta = []
    if result.get("query_file"):
        meta.append(f"query={result['query_file']}")
    if result.get("query_units") is not None:
        meta.append(f"query_units={result['query_units']}")
    if result.get("kb_units") is not None:
        meta.append(f"kb_units={result['kb_units']}")
    if meta:
        st.caption(" · ".join(meta))
    pairs = result.get("pairs") or []
    if not pairs:
        st.info("No similar pairs above threshold.")
        return
    st.dataframe(
        [
            {
                "score": round(p["score"], 3),
                "verdict": p["verdict"],
                "type": p["match_type"],
                "file_a": p["file_a"],
                "location_a": p["location_a"],
                "text_a": p["text_a"][:180],
                "file_b": p["file_b"],
                "location_b": p["location_b"],
                "text_b": p["text_b"][:180],
            }
            for p in pairs
        ],
        use_container_width=True,
    )
    with st.expander("Full pair details"):
        for i, p in enumerate(pairs[:50], start=1):
            st.markdown(
                f"**[{i}] {p['verdict']} · {p['score']:.3f} · {p['match_type']}**  \n"
                f"`{p['file_a']}` / {p['location_a']}  \n"
                f"> {p['text_a'][:400]}  \n"
                f"`{p['file_b']}` / {p['location_b']}  \n"
                f"> {p['text_b'][:400]}"
            )


def _proposal_panel() -> None:
    st.subheader("RFP → center draft")
    st.caption(
        "RFP를 분석하고 Knowledge Base 근거로 우리 센터 파트 초안을 만듭니다. "
        "전체 제안서 자동 완성이 아닙니다."
    )
    project_filter = st.text_input(
        "KB Project filter (optional)",
        key="prop_project",
        placeholder="e.g. DEMO-2026",
    )
    rfp_file = st.file_uploader(
        "RFP / 공고문",
        type=["pdf", "docx", "txt", "md"],
        key="prop_rfp_upload",
    )
    if st.button("Analyze RFP + Match Memory", type="primary", disabled=not rfp_file):
        with st.spinner("Parsing RFP and retrieving KB evidence…"):
            chunks, err = parse_rfp_bytes(rfp_file.getvalue(), rfp_file.name)
            if err and not chunks:
                st.error(err)
                return
            rfp = analyze_rfp(chunks)
            evidence = gather_kb_evidence(
                rfp,
                repo=repo,
                project_id=project_filter.strip() or None,
            )
            roles = suggest_roles(rfp, evidence)
            st.session_state["prop_rfp_result"] = rfp
            st.session_state["prop_evidence"] = [c.to_dict() for c in evidence]
            st.session_state["prop_roles"] = roles
            st.session_state.pop("prop_draft", None)
            st.session_state.pop("prop_selected", None)

    rfp = st.session_state.get("prop_rfp_result")
    if not rfp or not isinstance(rfp, dict):
        st.info("RFP를 업로드하고 분석을 실행하세요.")
        return

    st.markdown("### RFP analysis")
    cols = st.columns(2)
    cols[0].write(f"**사업명:** {rfp.get('project_name')}")
    cols[0].write(f"**발주:** {rfp.get('organization')}")
    cols[1].write(f"**기간:** {rfp.get('duration')}")
    cols[1].write(f"**예산:** {rfp.get('budget')}")
    st.write(f"**목적:** {rfp.get('purpose')}")
    if rfp.get("error"):
        st.warning(rfp["error"])
    with st.expander("요구사항 / KPI"):
        st.json(
            {
                "mandatory_requirements": rfp.get("mandatory_requirements"),
                "tech_requirements": rfp.get("tech_requirements"),
                "kpi": rfp.get("kpi"),
            }
        )

    evidence_dicts = st.session_state.get("prop_evidence") or []
    st.markdown(f"### KB evidence ({len(evidence_dicts)})")
    if not evidence_dicts:
        st.warning("Memory 근거가 없습니다. Library에서 센터 자료를 먼저 넣으세요.")
    else:
        for i, c in enumerate(evidence_dicts[:10], start=1):
            st.markdown(
                f"**[{i}] {c['filename']} / {c['location']}** "
                f"(score={c['score']:.3f})  \n{c['snippet'][:280]}"
            )

    roles = st.session_state.get("prop_roles") or []
    st.markdown("### Role candidates")
    role_labels = [f"{i + 1}. {r.get('role', '?')}" for i, r in enumerate(roles)]
    choice = st.radio("Select role", role_labels, key="prop_role_radio") if roles else None
    selected = roles[role_labels.index(choice)] if choice else None
    if selected:
        st.write(selected.get("reason", ""))
        st.caption(f"evidence: {selected.get('evidence', '')}")

    if st.button("Generate center draft", type="primary", disabled=not selected):
        with st.spinner("Generating draft from Memory evidence…"):
            cites = [
                Citation(
                    document_id=c["document_id"],
                    filename=c["filename"],
                    location=c["location"],
                    snippet=c["snippet"],
                    score=float(c["score"]),
                )
                for c in evidence_dicts
            ]
            draft = generate_draft(rfp, selected, cites)
            st.session_state["prop_draft"] = draft
            st.session_state["prop_selected"] = selected

    draft = st.session_state.get("prop_draft")
    selected = st.session_state.get("prop_selected") or selected
    if draft and selected:
        st.markdown("### Draft")
        for key, label in (
            ("necessity", "참여 필요성"),
            ("center_role", "담당 역할"),
            ("work_details", "수행내용"),
            ("deliverables", "산출물"),
            ("open_questions", "확인 필요"),
        ):
            st.markdown(f"**{label}**")
            st.write(draft.get(key, ""))
        cites = [
            Citation(
                document_id=c["document_id"],
                filename=c["filename"],
                location=c["location"],
                snippet=c["snippet"],
                score=float(c["score"]),
            )
            for c in evidence_dicts
        ]
        md = build_markdown(rfp, selected, draft, cites)
        st.download_button("Download Markdown", md, file_name="proposal_draft.md")
        docx_bytes = export_docx_bytes(rfp, selected, draft)
        st.download_button(
            "Download DOCX",
            docx_bytes,
            file_name="proposal_draft.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )


def _milestone_panel() -> None:
    st.subheader("Project tracking")
    st.caption(
        "과제·마일스톤을 등록하고, Memory에 적재된 산출물과 대조해 갭/지연을 추적합니다."
    )

    if st.button("Seed DEMO-2026 project + milestones"):
        result = seed_demo_project(repo=repo, project_id="DEMO-2026")
        st.success(result)

    with st.expander("Create / update project", expanded=False):
        pid = st.text_input("Project ID", value="DEMO-2026", key="mile_pid")
        title = st.text_input(
            "Title", value="Research Memory Platform 데모 과제", key="mile_title"
        )
        owner = st.text_input("Owner", key="mile_owner")
        c1, c2 = st.columns(2)
        start = c1.text_input("Start (YYYY-MM-DD)", key="mile_start")
        end = c2.text_input("End (YYYY-MM-DD)", key="mile_end")
        if st.button("Save project", key="mile_save_project"):
            repo.upsert_project(
                project_id=pid.strip(),
                title=title.strip() or pid.strip(),
                owner=owner.strip(),
                start_date=start.strip(),
                end_date=end.strip(),
            )
            st.success(f"Saved project {pid}")

    projects = repo.list_projects()
    if not projects:
        st.info("프로젝트가 없습니다. Seed 또는 Create project를 먼저 하세요.")
        return

    labels = {
        f"{p['project_id']} · {p.get('title')} (ms={p.get('milestone_count', 0)}, docs={p.get('document_count', 0)})": p[
            "project_id"
        ]
        for p in projects
    }
    choice = st.selectbox("Project", list(labels.keys()), key="mile_project_select")
    project_id = labels[choice]

    with st.expander("Add milestone"):
        m_title = st.text_input("Milestone title", key="mile_new_title")
        m_due = st.text_input("Due date (YYYY-MM-DD)", key="mile_new_due")
        m_type = st.selectbox(
            "Deliverable type",
            ["proposal", "paper", "note", "meeting", "excel", "other"],
            key="mile_new_type",
        )
        m_kw = st.text_input(
            "Expected keywords (comma/space separated)",
            key="mile_new_kw",
            placeholder="Research Memory, Pipeline",
        )
        if st.button("Add milestone", key="mile_add") and m_title.strip():
            mid = repo.add_milestone(
                project_id=project_id,
                title=m_title.strip(),
                due_date=m_due.strip(),
                deliverable_type=m_type,
                expected_keywords=m_kw.strip(),
            )
            st.success(f"Added milestone {mid[:8]}")
            st.rerun()

    timeline = project_timeline(project_id, repo=repo)
    report = gap_report(project_id, repo=repo)
    summary = report.get("summary") or {}
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Milestones", summary.get("milestones", 0))
    m2.metric("Covered", summary.get("covered", 0))
    m3.metric("Missing", summary.get("missing", 0))
    m4.metric("Overdue", summary.get("overdue", 0))

    st.markdown("### Gap report")
    gaps = report.get("gaps") or []
    if gaps:
        st.dataframe(
            [
                {
                    "title": g["title"],
                    "due": g["due_date"],
                    "type": g["deliverable_type"],
                    "coverage": g["coverage"],
                    "status": g["effective_status"],
                    "matched": g["matched_filename"] or "-",
                    "reason": g["match_reason"] or "-",
                }
                for g in gaps
            ],
            use_container_width=True,
        )
    else:
        st.write("No milestones yet.")

    c_a, c_b = st.columns(2)
    if c_a.button("Auto-link covered milestones", key="mile_autolink"):
        linked = auto_link_milestones(project_id, repo=repo)
        st.success(f"updated={linked.get('updated')}")
        st.rerun()

    st.markdown("### Timeline")
    for ev in timeline.get("events") or []:
        st.markdown(
            f"- `{ev.get('date') or 'n/a'}` · **{ev['kind']}** · {ev.get('title')} "
            f"({ev.get('status')}/{ev.get('deliverable_type')})"
        )

    unlinked = report.get("orphan_documents") or []
    if unlinked:
        st.markdown("### Unlinked documents — no milestone match")
        for d in unlinked:
            st.write(
                f"- {d['filename']} · type={d.get('doc_type')} · {d.get('created_at', '')[:10]}"
            )


if __name__ == "__main__":
    main()
