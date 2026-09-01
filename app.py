from __future__ import annotations

import hashlib
import importlib
import re
import sys
from datetime import date, datetime, time
from html import escape
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
from research_memory.engine import research_note as research_note_engine

# Streamlit can keep a stale research_note module across edits; force reload
# when expected exports are missing.
if not hasattr(research_note_engine, "stt_available"):
    sys.modules.pop("research_memory.engine.research_note", None)
    research_note_engine = importlib.import_module("research_memory.engine.research_note")

MODE_LABELS = research_note_engine.MODE_LABELS
MODE_MEETING = research_note_engine.MODE_MEETING
MODE_RESEARCH = research_note_engine.MODE_RESEARCH
build_research_note_docx = research_note_engine.build_research_note_docx
build_research_note_hwpx = research_note_engine.build_research_note_hwpx
generate_research_note_summary = research_note_engine.generate_research_note_summary
hwpx_available = research_note_engine.hwpx_available
meeting_as_markdown = research_note_engine.meeting_as_markdown
meeting_rows = research_note_engine.meeting_rows
note_as_markdown = research_note_engine.note_as_markdown
note_rows = research_note_engine.note_rows
parse_meeting_note_fields = research_note_engine.parse_meeting_note_fields
parse_research_note_fields = research_note_engine.parse_research_note_fields
stt_available = research_note_engine.stt_available
transcribe_audio_bytes = research_note_engine.transcribe_audio_bytes
from research_memory.pipeline.extractors import extract_chunks
from research_memory.pipeline.ingest import ingest_bytes
from research_memory.engine.proposal import (
    analyze_rfp,
    build_budget_plan_section,
    build_markdown,
    clean_draft_prose,
    export_docx_bytes,
    gather_kb_evidence_split,
    generate_draft,
    parse_budget_amount_cheon,
    parse_rfp_bytes,
    review_draft_quality,
    revise_draft_from_review,
    suggest_roles,
)
from research_memory.engine.similarity import (
    compare_kb_documents,
    compare_upload_vs_kb,
    compare_uploads,
)
from research_memory.engine import schedule as schedule_engine

if not hasattr(schedule_engine, "CALENDAR_CLICK_JS") or not hasattr(
    schedule_engine, "layout_week_bars"
):
    schedule_engine = importlib.reload(schedule_engine)

from research_memory.engine.schedule import (
    EVENT_TYPE_LABELS,
    EVENT_TYPES,
    STATUS_LABELS,
    STATUSES,
    calendar_grid_sunday,
    chip_time_and_title,
    grid_date_bounds,
    item_overlaps_month,
    items_by_date,
    mount_schedule_calendar,
    normalize_event_type,
    normalize_status,
    render_calendar_html,
    shift_month,
)
from research_memory.kb.repository import KnowledgeRepository
from research_memory.schema import (
    ROLE_PROJECT,
    ROLE_REFERENCE,
    Citation,
    normalize_document_role,
)

st.set_page_config(
    page_title="Research Memory Platform",
    layout="wide",
)

ensure_data_dirs()
repo = KnowledgeRepository()

PAGE_HOME = "Home"
PAGE_CHAT = "Research Chat"
PAGE_PROPOSAL = "Proposal Intelligence"
PAGE_SIMILARITY = "Similarity Intelligence"
PAGE_SCHEDULE = "Project Schedule"
PAGE_RESEARCH_NOTE = "Research Note"
PAGE_CODING_AGENT = "Coding Agent"

MAIN_NAV = [
    (PAGE_HOME, "🏠 홈"),
    (PAGE_SCHEDULE, "📅 일정 관리"),
    (PAGE_CHAT, "💬 채팅"),
    (PAGE_RESEARCH_NOTE, "📝 연구 기록"),
    (PAGE_PROPOSAL, "🖊️ 제안서"),
    (PAGE_SIMILARITY, "🔍 유사도 검토"),
]

DEV_NAV = [
    (PAGE_CODING_AGENT, "💻 코딩 에이전트"),
]


def _go(page: str, *, focus_upload: bool = False, doc_id: str | None = None) -> None:
    if focus_upload:
        st.session_state.home_focus_upload = True
        page = PAGE_HOME
    st.session_state.page = page
    if doc_id:
        st.session_state.library_selected_id = doc_id
    st.rerun()


def _citation_from_dict(payload: dict) -> Citation:
    return Citation(
        document_id=str(payload.get("document_id", "")),
        filename=str(payload.get("filename", "")),
        location=str(payload.get("location", "")),
        snippet=str(payload.get("snippet", "")),
        score=float(payload.get("score") or 0.0),
        document_role=normalize_document_role(payload.get("document_role")),
    )


def _citations_from_dicts(items: list[dict]) -> list[Citation]:
    return [_citation_from_dict(item) for item in items]


def _document_role_of(doc_or_cite: dict | None) -> str:
    if not doc_or_cite:
        return ROLE_PROJECT
    return normalize_document_role(doc_or_cite.get("document_role"))


def _role_badge(doc_or_cite: dict | None, *, doc_type: str | None = None) -> str:
    """Human label for Library / Evidence: [연구문서] / [참고자료] / [참고규정]."""
    role = _document_role_of(doc_or_cite)
    dtype = (doc_type or (doc_or_cite or {}).get("doc_type") or "").strip().lower()
    if role == ROLE_REFERENCE:
        return "[참고규정]" if dtype == "regulation" else "[참고자료]"
    return "[연구문서]"


def _kb_stats() -> dict:
    docs = repo.list_documents()
    projects = repo.list_projects()
    chunk_count = sum(int(d.get("chunk_count") or 0) for d in docs)
    return {
        "docs": docs,
        "doc_count": len(docs),
        "project_count": len(projects),
        "chunk_count": chunk_count,
        "last_indexed": _last_indexed_label(),
    }


def _last_indexed_label() -> str:
    times: list[float] = []
    for path in (VECTOR_INDEX_PATH, INDEX_PATH):
        if path.exists():
            times.append(path.stat().st_mtime)
    if not times:
        return "—"
    return datetime.fromtimestamp(max(times)).strftime("%Y-%m-%d %H:%M")


def _inject_ui_css() -> None:
    st.markdown(
        """
<style>
/* Unify expander header height across Chat / Proposal / Library */
div[data-testid="stExpander"] > details {
  border: 1px solid rgba(49, 51, 63, 0.18) !important;
  border-radius: 0.5rem !important;
  background: rgba(250, 250, 252, 0.95);
}
div[data-testid="stExpander"] > details > summary {
  min-height: 2.75rem !important;
  display: flex !important;
  align-items: center !important;
  padding-top: 0.55rem !important;
  padding-bottom: 0.55rem !important;
}
div[data-testid="stExpander"] > details > summary p,
div[data-testid="stExpander"] > details > summary span {
  font-size: 0.95rem !important;
  line-height: 1.35 !important;
  margin: 0 !important;
}
/* Fixed-size evidence / citation cards */
.rm-card {
  height: 7.25rem;
  overflow-y: auto;
  padding: 0.65rem 0.75rem;
  margin: 0 0 0.55rem 0;
  border: 1px solid rgba(49, 51, 63, 0.14);
  border-radius: 0.45rem;
  background: #fff;
  font-size: 0.9rem;
  line-height: 1.4;
  box-sizing: border-box;
}
.rm-card strong { font-size: 0.9rem; }
.rm-card .rm-meta {
  color: rgba(49, 51, 63, 0.65);
  font-size: 0.8rem;
  margin: 0.15rem 0 0.35rem 0;
}
.rm-card .rm-snip {
  color: rgba(49, 51, 63, 0.92);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
        """,
        unsafe_allow_html=True,
    )


def _inject_schedule_calendar_css() -> None:
    st.markdown(
        """
<style>
.rm-cal-nav-label {
  min-width: 9rem;
  text-align: center;
  font-size: 1.05rem;
  font-weight: 700;
  color: #0f172a;
  margin: 0;
}
div[data-testid="stDialog"] div[role="dialog"] {
  width: min(40rem, 96vw) !important;
  max-width: 40rem !important;
}
div[data-testid="stDialog"] .rm-dlg-kicker {
  font-size: 0.8rem;
  font-weight: 700;
  color: #64748b;
  margin: 0 0 0.55rem;
}
div[data-testid="stDialog"] div[data-testid="column"]:has(.rm-dlg-toggle-mark) {
  border: 1px solid #e2e8f0;
  border-radius: 0.75rem;
  background: #ffffff;
  padding: 0.7rem 0.85rem 0.55rem !important;
}
div[data-testid="stDialog"] div[data-testid="column"]:has(.rm-dlg-toggle-mark.on) {
  border-color: #93c5fd;
  background: #eff6ff;
}
div[data-testid="stDialog"] .rm-dlg-toggle-title {
  font-size: 0.92rem;
  font-weight: 700;
  color: #0f172a;
  margin: 0;
}
div[data-testid="stDialog"] .rm-dlg-toggle-sub {
  font-size: 0.75rem;
  color: #64748b;
  margin: 0.1rem 0 0.25rem;
}
div[data-testid="stDialog"] .rm-dlg-repeat {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #ecfdf5;
  border-radius: 0.75rem;
  padding: 0.7rem 0.9rem;
  margin: 0.35rem 0 0.15rem;
  color: #166534;
  font-weight: 600;
}
div[data-testid="stDialog"] .rm-dlg-repeat span {
  font-size: 0.75rem;
  font-weight: 500;
  color: #64748b;
}
div[data-testid="stDialog"] .element-container:has(.rm-dlg-btn.complete) + .element-container button {
  color: #16a34a !important;
  background: #ffffff !important;
  border: 1px solid #86efac !important;
}
div[data-testid="stDialog"] .element-container:has(.rm-dlg-btn.delete) + .element-container button {
  color: #dc2626 !important;
  background: #ffffff !important;
  border: 1px solid #fca5a5 !important;
}
</style>
        """,
        unsafe_allow_html=True,
    )


def _sched_handle_query_params() -> None:
    qp = st.query_params
    sched_item = qp.get("sched_item")
    sched_date = qp.get("sched_date")
    sched_view = qp.get("sched_view")
    if not sched_item and not sched_date and not sched_view:
        return
    st.session_state.page = PAGE_SCHEDULE
    if sched_item:
        st.session_state.sched_selected_item_id = sched_item
        if sched_date:
            st.session_state.sched_selected_date = sched_date
    elif sched_date:
        st.session_state.sched_selected_date = sched_date
        st.session_state.pop("sched_selected_item_id", None)
        st.session_state.sched_open_add = True
    for key in ("sched_item", "sched_date", "sched_view"):
        if key in qp:
            del qp[key]
    st.rerun()


def _sched_clear_selection() -> None:
    st.session_state.pop("sched_selected_item_id", None)
    st.session_state.pop("sched_selected_date", None)
    st.session_state.pop("sched_open_add", None)
    st.session_state.pop("sched_dlg_loaded", None)


def _sched_parse_date(value: str | None, fallback: date) -> date:
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return fallback


def _sched_parse_time(value: str | None) -> time:
    try:
        hh, mm = (value or "09:00").split(":")[:2]
        return time(int(hh), int(mm))
    except ValueError:
        return time(9, 0)


def _sched_init_dialog_state(
    *,
    mode: str,
    item: dict | None,
    add_date: date | None,
    projects: list[dict],
) -> None:
    load_key = item["id"] if mode == "edit" and item else f"new:{(add_date or date.today()).isoformat()}"
    if st.session_state.get("sched_dlg_loaded") == load_key:
        return
    if mode == "edit" and item:
        time_str, base_title = chip_time_and_title(item.get("title"), item.get("note"))
        st.session_state.sched_dlg_title = base_title
        st.session_state.sched_dlg_note = item.get("note") or ""
        st.session_state.sched_dlg_all_day = time_str is None
        st.session_state.sched_dlg_time = _sched_parse_time(time_str)
        start_d = _sched_parse_date(item.get("date"), date.today())
        end_d = _sched_parse_date(item.get("end_date"), start_d)
        st.session_state.sched_dlg_date = start_d
        st.session_state.sched_dlg_end_date = end_d
        st.session_state.sched_dlg_range = end_d != start_d
        st.session_state.sched_dlg_type = normalize_event_type(item.get("event_type"))
        st.session_state.sched_dlg_status = normalize_status(item.get("status"))
        st.session_state.sched_dlg_project = item.get("project_id") or projects[0]["project_id"]
    else:
        st.session_state.sched_dlg_title = ""
        st.session_state.sched_dlg_note = ""
        st.session_state.sched_dlg_all_day = True
        st.session_state.sched_dlg_time = time(9, 0)
        day = add_date or date.today()
        st.session_state.sched_dlg_date = day
        st.session_state.sched_dlg_end_date = day
        st.session_state.sched_dlg_range = False
        st.session_state.sched_dlg_type = "task"
        st.session_state.sched_dlg_status = "planned"
        st.session_state.sched_dlg_project = projects[0]["project_id"]
    st.session_state.sched_dlg_loaded = load_key


def _sched_composed_title() -> str:
    title = (st.session_state.get("sched_dlg_title") or "").strip()
    if not title:
        return ""
    if st.session_state.get("sched_dlg_all_day", True):
        return title
    tval = st.session_state.get("sched_dlg_time") or time(9, 0)
    return f"{tval.strftime('%H:%M')} {title}"


def _sched_dialog_dates() -> tuple[str, str]:
    start = st.session_state.sched_dlg_date
    if st.session_state.get("sched_dlg_range"):
        end = st.session_state.get("sched_dlg_end_date") or start
    else:
        end = start
    return start.isoformat(), end.isoformat()


def _schedule_task_form(
    *,
    mode: str,
    item: dict | None,
    add_date: date | None,
    projects: list[dict],
    project_map: dict[str, dict],
) -> None:
    _sched_init_dialog_state(mode=mode, item=item, add_date=add_date, projects=projects)
    project_ids = [p["project_id"] for p in projects]
    if st.session_state.sched_dlg_project not in project_ids:
        st.session_state.sched_dlg_project = project_ids[0]

    st.markdown('<div class="rm-dlg-kicker">업무</div>', unsafe_allow_html=True)
    st.text_input("제목 *", key="sched_dlg_title")
    st.text_area("설명", key="sched_dlg_note", height=92)

    t1, t2 = st.columns(2)
    with t1:
        on_cls = "on" if st.session_state.get("sched_dlg_all_day", True) else ""
        st.markdown(
            f'<span class="rm-dlg-toggle-mark {on_cls}"></span>'
            '<p class="rm-dlg-toggle-title">종일</p>'
            '<p class="rm-dlg-toggle-sub">시각 없이 날짜만</p>',
            unsafe_allow_html=True,
        )
        st.toggle("종일", key="sched_dlg_all_day", label_visibility="collapsed")
    with t2:
        range_on = bool(st.session_state.get("sched_dlg_range"))
        on_cls = "on" if range_on else ""
        st.markdown(
            f'<span class="rm-dlg-toggle-mark {on_cls}"></span>'
            '<p class="rm-dlg-toggle-title">기간</p>'
            '<p class="rm-dlg-toggle-sub">시작 · 종료</p>',
            unsafe_allow_html=True,
        )
        st.toggle("기간", key="sched_dlg_range", label_visibility="collapsed")

    if st.session_state.get("sched_dlg_range"):
        if "sched_dlg_end_date" not in st.session_state:
            st.session_state.sched_dlg_end_date = st.session_state.sched_dlg_date
        d1, d2 = st.columns(2)
        with d1:
            st.date_input("시작일 *", key="sched_dlg_date", format="YYYY-MM-DD")
        with d2:
            st.date_input("종료일 *", key="sched_dlg_end_date", format="YYYY-MM-DD")
    else:
        st.date_input("날짜 *", key="sched_dlg_date", format="YYYY-MM-DD")
    if not st.session_state.get("sched_dlg_all_day", True):
        st.time_input("시작 시각", key="sched_dlg_time", step=60)

    s1, s2 = st.columns(2)
    with s1:
        st.selectbox(
            "유형",
            list(EVENT_TYPES),
            format_func=lambda x: EVENT_TYPE_LABELS[x],
            key="sched_dlg_type",
        )
    with s2:
        st.selectbox(
            "상태",
            list(STATUSES),
            format_func=lambda x: STATUS_LABELS[x],
            key="sched_dlg_status",
        )
    st.selectbox(
        "과제",
        project_ids,
        format_func=lambda pid: f"{pid} · {(project_map.get(pid) or {}).get('title') or pid}",
        key="sched_dlg_project",
    )
    st.markdown(
        '<div class="rm-dlg-repeat">🔄 반복 <span>준비 중</span></div>',
        unsafe_allow_html=True,
    )

    st.markdown("")
    if mode == "edit":
        assert item is not None
        item_id = item["id"]
        b1, b2, _sp, b3, b4 = st.columns([1.15, 1.15, 1.4, 0.85, 0.85])
        with b1:
            st.markdown('<span class="rm-dlg-btn complete"></span>', unsafe_allow_html=True)
            if st.button("✓ 업무 완료", key="sched_dlg_complete", use_container_width=True):
                repo.update_schedule_item(item_id, status="done")
                _sched_clear_selection()
                st.rerun()
        with b2:
            st.markdown('<span class="rm-dlg-btn delete"></span>', unsafe_allow_html=True)
            if st.button("업무 삭제", key="sched_dlg_delete", use_container_width=True):
                repo.delete_schedule_item(item_id)
                _sched_clear_selection()
                st.rerun()
        with b3:
            if st.button("닫기", key="sched_dlg_close", use_container_width=True):
                _sched_clear_selection()
                st.rerun()
        with b4:
            if st.button("저장", key="sched_dlg_save", type="primary", use_container_width=True):
                title = _sched_composed_title()
                if not title:
                    st.warning("제목을 입력하세요.")
                else:
                    start_s, end_s = _sched_dialog_dates()
                    repo.update_schedule_item(
                        item_id,
                        title=title,
                        date=start_s,
                        end_date=end_s,
                        event_type=normalize_event_type(st.session_state.sched_dlg_type),
                        status=normalize_status(st.session_state.sched_dlg_status),
                        project_id=st.session_state.sched_dlg_project,
                        note=(st.session_state.sched_dlg_note or "").strip(),
                    )
                    _sched_clear_selection()
                    st.rerun()
    else:
        _sp, b3, b4 = st.columns([3.7, 0.85, 0.85])
        with b3:
            if st.button("닫기", key="sched_dlg_close_add", use_container_width=True):
                _sched_clear_selection()
                st.rerun()
        with b4:
            if st.button("등록", key="sched_dlg_create", type="primary", use_container_width=True):
                title = _sched_composed_title()
                if not title:
                    st.warning("제목을 입력하세요.")
                else:
                    start_s, end_s = _sched_dialog_dates()
                    repo.add_schedule_item(
                        project_id=st.session_state.sched_dlg_project,
                        title=title,
                        event_type=normalize_event_type(st.session_state.sched_dlg_type),
                        date=start_s,
                        end_date=end_s,
                        status=normalize_status(st.session_state.sched_dlg_status),
                        note=(st.session_state.sched_dlg_note or "").strip(),
                    )
                    _sched_clear_selection()
                    st.rerun()


@st.dialog("업무 수정", width="medium", on_dismiss=_sched_clear_selection)
def _schedule_edit_dialog(
    item: dict,
    projects: list[dict],
    project_map: dict[str, dict],
) -> None:
    _schedule_task_form(
        mode="edit",
        item=item,
        add_date=None,
        projects=projects,
        project_map=project_map,
    )


@st.dialog("일정 추가", width="medium", on_dismiss=_sched_clear_selection)
def _schedule_add_dialog(
    add_date: date,
    projects: list[dict],
    project_map: dict[str, dict],
) -> None:
    _schedule_task_form(
        mode="add",
        item=None,
        add_date=add_date,
        projects=projects,
        project_map=project_map,
    )


def main() -> None:
    _inject_ui_css()
    if "page" not in st.session_state:
        st.session_state.page = PAGE_HOME
    _sched_handle_query_params()

    page = st.session_state.page
    if page == "Library":
        st.session_state.page = PAGE_HOME
        page = PAGE_HOME

    if page == PAGE_CODING_AGENT:
        from coding_agent.ui import run_coding_agent_app

        run_coding_agent_app(on_home=lambda: _go(PAGE_HOME))
        return

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
        st.caption("Being Developed")
        for page_id, label in DEV_NAV:
            active = st.session_state.page == page_id
            if st.button(
                label,
                key=f"nav-{page_id}",
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
        if MOCK_LLM:
            st.warning("RM_MOCK_LLM=true")
        with st.expander("고급", expanded=False):
            st.caption(f"검색 임베딩: `{EMBED_MODEL}`")
            if st.button("검색 인덱스 재구축", use_container_width=True, key="sidebar_rebuild_index"):
                with st.spinner("Embedding chunks…"):
                    n = repo.rebuild_index()
                st.success(f"chunks={n} · {repo.last_index_status}")

    if page == PAGE_HOME:
        _home_page()
    elif page == PAGE_CHAT:
        _chat_page()
    elif page == PAGE_RESEARCH_NOTE:
        st.title("연구 기록")
        st.caption("연구노트·회의록 초안을 Memory/새 자료로 만들고 표로 저장합니다.")
        _research_note_panel()
    elif page == PAGE_PROPOSAL:
        st.title("제안서")
        st.caption("RFP와 Memory 근거로 제안 초안을 작성·검토합니다.")
        _proposal_panel()
    elif page == PAGE_SIMILARITY:
        st.title("유사도 검토")
        st.caption(
            "새 초안·문서가 Memory(또는 다른 문서)와 얼마나 겹치는지 "
            "문장·페이지·이미지로 확인합니다."
        )
        _similarity_panel()
    elif page == PAGE_SCHEDULE:
        st.title("일정 관리")
        st.caption("과제별 회의·제출·작업·마일스톤을 월간 캘린더로 관리합니다.")
        _schedule_panel()
    else:
        _home_page()


def _home_page() -> None:
    stats = _kb_stats()
    docs = stats["docs"]
    focus_upload = bool(st.session_state.pop("home_focus_upload", False))

    selected_id = st.session_state.get("library_selected_id")
    if selected_id and any(d["id"] == selected_id for d in docs):
        _document_detail(selected_id, docs)
        return

    if st.session_state.get("library_view") == "project_docs":
        if st.button("← Projects", key="home_to_projects_from_docs"):
            st.session_state.library_view = "projects"
            st.session_state.pop("library_project_focus", None)
            st.rerun()
        pid = st.session_state.get("library_project_focus") or ""
        _library_project_docs_view(docs, pid)
        return

    empty = stats["doc_count"] == 0

    st.title("Research Memory")
    if empty:
        st.subheader(
            "Build an organizational research memory that understands your documents "
            "and answers with evidence."
        )
    else:
        st.caption("Organizational research dashboard")

    if empty:
        c1, c2, c3 = st.columns(3)
        c1.metric("Documents", 0)
        c2.metric("Projects", stats["project_count"])
        c3.metric("Knowledge Chunks", 0)
        st.info("과제는 준비되어 있습니다. 아래에서 프로젝트를 열거나 자료를 업로드하세요.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Documents", stats["doc_count"])
        c2.metric("Projects", stats["project_count"])
        c3.metric("Chunks", stats["chunk_count"])
        c4.metric("Last Updated", stats["last_indexed"])

    _library_projects_view(docs)

    if not empty:
        st.markdown("---")
        st.markdown("### Search")
        with st.expander("Search documents", expanded=False):
            _library_search_view(docs, show_heading=False)

    st.markdown("---")
    st.markdown("### Upload")
    with st.expander("Upload Documents", expanded=focus_upload or empty):
        _upload_panel()

    if not empty:
        st.markdown("---")
        st.markdown("#### Recent Documents")
        for d in docs[:3]:
            label = d.get("title") or d["filename"]
            day = str(d.get("created_at") or "")[:10] or "—"
            cols = st.columns([5, 1])
            cols[0].markdown(f"**{label}**  \n{day}")
            if cols[1].button("Open", key=f"home-open-{d['id']}"):
                _go(PAGE_HOME, doc_id=d["id"])

    if empty:
        st.markdown("---")
        st.markdown("### How It Works")
        st.markdown(
            """
1. **Upload Documents** — add proposals, notes, papers, and meeting records  
↓  
2. **Browse Projects** — open a project folder to explore Memory  
↓  
3. **Ask with Evidence** — Research Chat answers only with citations
"""
        )


def _library_projects_view(docs: list) -> None:
    head, add = st.columns([10, 1])
    head.markdown("### Projects")
    if add.button("+", key="proj-add", help="새 프로젝트 폴더 추가", use_container_width=True, type="primary"):
        st.session_state.proj_creating = True
        st.rerun()
    st.caption("Open a project to see its documents in Memory.")

    if st.session_state.get("proj_creating"):
        new_folder = st.text_input(
            "폴더명",
            placeholder="예: Manufacturing-X",
            key="proj_create_name",
            help="Library 카드에 크게 보이는 짧은 이름",
        )
        new_full = st.text_input(
            "과제 full name (소제목)",
            placeholder="예: 한국형 Manufacturing-X 온톨로지 구축",
            key="proj_create_title",
            help="폴더명 아래 소제목으로 표시됩니다",
        )
        c1, c2 = st.columns(2)
        if c1.button("추가", key="proj-create-save", use_container_width=True):
            folder = (new_folder or "").strip()
            full_name = (new_full or "").strip() or folder
            existing = {p.get("project_id") for p in repo.list_projects()}
            existing |= {
                (d.get("project_id") or "").strip()
                for d in docs
                if (d.get("project_id") or "").strip()
            }
            if not folder:
                st.error("폴더명을 입력하세요.")
            elif folder == "(No project)":
                st.error("이 이름은 사용할 수 없습니다.")
            elif folder in existing:
                st.error(f"이미 있는 프로젝트입니다: {folder}")
            else:
                repo.upsert_project(project_id=folder, title=full_name, status="active")
                st.session_state.proj_creating = False
                st.session_state.pop("proj_create_name", None)
                st.session_state.pop("proj_create_title", None)
                st.rerun()
        if c2.button("취소", key="proj-create-cancel", use_container_width=True):
            st.session_state.proj_creating = False
            st.session_state.pop("proj_create_name", None)
            st.session_state.pop("proj_create_title", None)
            st.rerun()

    project_meta = {
        (p.get("project_id") or "").strip(): p
        for p in repo.list_projects()
        if (p.get("project_id") or "").strip()
    }

    groups = _group_docs_by_project(docs)
    # Registered projects should appear even when empty (0 documents).
    for pid in project_meta:
        if pid not in groups:
            groups[pid] = []
    # Hide empty orphan bucket.
    if "(No project)" in groups and not groups["(No project)"]:
        groups.pop("(No project)", None)

    items = sorted(
        groups.items(),
        key=lambda kv: (kv[0] == "(No project)", kv[0]),
    )
    if not items:
        st.info("등록된 프로젝트가 없습니다. + 로 새 폴더를 추가하세요.")
        return

    # 3-column compact card grid
    for i in range(0, len(items), 3):
        cols = st.columns(3)
        for j, col in enumerate(cols):
            if i + j >= len(items):
                break
            pid, group = items[i + j]
            with col:
                _project_card(pid, group, meta=project_meta.get(pid))


def _project_card(project_id: str, docs: list, meta: dict | None = None) -> None:
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
    is_orphan = project_id == "(No project)"
    safe = hashlib.md5(project_id.encode("utf-8")).hexdigest()[:12]
    meta = meta or {}
    full_name = (meta.get("title") or "").strip()
    subtitle = full_name if full_name and full_name != project_id else ""

    title_c, edit_c, del_c = st.columns([6, 1, 1])
    title_html = (
        f"<div style='font-size:16px;font-weight:700;color:#111827;padding-top:4px;'>"
        f"{_html_esc(project_id)}</div>"
    )
    if subtitle:
        title_html += (
            f"<div style='font-size:13px;color:#6b7280;margin-top:2px;line-height:1.35;'>"
            f"{_html_esc(subtitle)}</div>"
        )
    title_c.markdown(title_html, unsafe_allow_html=True)
    if not is_orphan:
        if edit_c.button("✎", key=f"proj-edit-{safe}", help="폴더명 / 과제명 변경", use_container_width=True):
            st.session_state[f"proj_renaming_{safe}"] = True
            st.session_state[f"proj_rename_src_{safe}"] = project_id
            st.rerun()
        if del_c.button("✕", key=f"proj-del-{safe}", help="프로젝트 폴더 삭제", use_container_width=True, type="primary"):
            st.session_state[f"proj_confirm_del_{safe}"] = True
            st.session_state[f"proj_del_src_{safe}"] = project_id
            st.rerun()

    st.caption(f"자료 {doc_n}건 · 연구노트 {note_n}개 · {year_s} · {latest or '—'}")

    if st.session_state.get(f"proj_renaming_{safe}") and st.session_state.get(f"proj_rename_src_{safe}") == project_id:
        new_folder = st.text_input(
            "폴더명",
            value=project_id,
            key=f"proj-rename-input-{safe}",
            help="Library 카드에 크게 보이는 짧은 이름",
        )
        new_full = st.text_input(
            "과제 full name (소제목)",
            value=full_name or project_id,
            key=f"proj-rename-title-{safe}",
            help="폴더명 아래 소제목으로 표시됩니다",
        )
        s1, s2 = st.columns(2)
        if s1.button("저장", key=f"proj-rename-save-{safe}", use_container_width=True):
            try:
                folder = (new_folder or "").strip()
                title_val = (new_full or "").strip() or folder
                if not folder:
                    raise ValueError("폴더명을 입력하세요.")
                if folder == "(No project)":
                    raise ValueError("이 이름은 사용할 수 없습니다.")
                if folder != project_id:
                    repo.rename_project(project_id, folder)
                existing = repo.get_project(folder) or {}
                repo.upsert_project(
                    project_id=folder,
                    title=title_val,
                    owner=existing.get("owner") or "",
                    start_date=existing.get("start_date") or "",
                    end_date=existing.get("end_date") or "",
                    status=existing.get("status") or "active",
                    notes=existing.get("notes") or "",
                )
                st.session_state.pop(f"proj_renaming_{safe}", None)
                st.session_state.pop(f"proj_rename_src_{safe}", None)
                if st.session_state.get("library_project_focus") == project_id:
                    st.session_state.library_project_focus = folder
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
        if s2.button("취소", key=f"proj-rename-cancel-{safe}", use_container_width=True):
            st.session_state.pop(f"proj_renaming_{safe}", None)
            st.session_state.pop(f"proj_rename_src_{safe}", None)
            st.rerun()

    if st.session_state.get(f"proj_confirm_del_{safe}") and st.session_state.get(f"proj_del_src_{safe}") == project_id:
        st.warning(f"`{project_id}` 폴더와 자료 {doc_n}건을 삭제할까요?")
        d1, d2 = st.columns(2)
        if d1.button("삭제", key=f"proj-del-yes-{safe}", type="primary", use_container_width=True):
            repo.delete_project_folder(project_id)
            st.session_state.pop(f"proj_confirm_del_{safe}", None)
            st.session_state.pop(f"proj_del_src_{safe}", None)
            if st.session_state.get("library_project_focus") == project_id:
                st.session_state.pop("library_project_focus", None)
                st.session_state.library_view = "projects"
            st.rerun()
        if d2.button("취소", key=f"proj-del-no-{safe}", use_container_width=True):
            st.session_state.pop(f"proj_confirm_del_{safe}", None)
            st.session_state.pop(f"proj_del_src_{safe}", None)
            st.rerun()

    if st.button("Open", key=f"proj-open-{safe}", use_container_width=True):
        st.session_state.library_view = "project_docs"
        st.session_state.library_project_focus = project_id
        st.session_state.pop("library_selected_id", None)
        st.session_state.page = PAGE_HOME
        st.rerun()
    st.markdown(
        "<div style='border-bottom:1px solid #e5e7eb;margin:10px 0 14px;'></div>",
        unsafe_allow_html=True,
    )


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
    proj = repo.get_project(project_id) or {}
    full_name = (proj.get("title") or "").strip()
    if full_name and full_name != project_id:
        st.caption(full_name)
    if project_id == "(No project)":
        group = [d for d in docs if not (d.get("project_id") or "").strip()]
    else:
        group = [d for d in docs if (d.get("project_id") or "").strip() == project_id]

    st.caption(f"{len(group)} documents in this project")
    group = _sort_documents(group, key_prefix=f"proj-{project_id}")
    _library_doc_list(group, key_prefix=f"proj-{project_id}")


def _library_search_view(docs: list, *, show_heading: bool = True) -> None:
    if show_heading:
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

    role_f = st.selectbox(
        "역할",
        ["전체", "연구문서", "참고자료"],
        key="lib_role",
    )
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

    role_value = None
    if role_f == "연구문서":
        role_value = ROLE_PROJECT
    elif role_f == "참고자료":
        role_value = ROLE_REFERENCE

    filtered = _filter_documents(
        docs,
        query=q,
        doc_type=None if type_f == "All" else type_f,
        project_id=None if project_f == "All" else project_f,
        year=None if year_f == "All" else year_f,
        status=None if status_f == "All" else status_f,
        document_role=role_value,
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
        badge = _role_badge(d)
        meta = (
            f"{badge} · `{d['filename']}` · {d.get('doc_type') or '—'} · "
            f"{d.get('project_id') or '—'} · {d.get('year') or '—'} · "
            f"{d.get('status')} · {str(d.get('created_at') or '')[:10]}"
        )
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"**{title}**  \n{meta}")
        if c2.button("Open", key=f"{key_prefix}-open-{d['id']}", use_container_width=True):
            _go(PAGE_HOME, doc_id=d["id"])


def _filter_documents(
    docs: list,
    *,
    query: str,
    doc_type: str | None,
    project_id: str | None,
    year: str | None,
    status: str | None,
    document_role: str | None = None,
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
        if document_role and _document_role_of(d) != document_role:
            continue
        if q:
            blob = " ".join(
                [
                    str(d.get("title") or ""),
                    str(d.get("filename") or ""),
                    str(d.get("project_id") or ""),
                    str(d.get("doc_type") or ""),
                    str(d.get("document_role") or ""),
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
        st.session_state.chat_focus_document_id = doc_id
        st.session_state.chat_prefill = (
            f"{title} 문서 기준으로, Memory 근거를 들어 핵심 내용을 요약해 주세요."
        )
        _go(PAGE_CHAT)
    if top[2].button(
        "Delete",
        type="primary",
        use_container_width=True,
        key=f"del-top-{doc_id}",
    ):
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
        f"{_role_badge(doc)} · `{doc['filename']}` · {doc.get('doc_type') or '—'} · "
        f"{doc.get('project_id') or '—'} · {doc.get('year') or '—'} · "
        f"{doc.get('status')}"
    )

    _document_insight_card(doc)

    tab_preview, tab_sum, tab_related = st.tabs(["Preview", "Summary", "Related"])

    with tab_preview:
        preview_mode = st.radio(
            "보기",
            ["원본 보기", "추출 텍스트"],
            horizontal=True,
            key=f"lib-preview-mode-{doc_id}",
            help="원본 보기=업로드 파일 형태 · 추출 텍스트=검색/편집용 평문",
        )
        if preview_mode == "원본 보기":
            _render_original_preview(doc)
        else:
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
                role_options = ["연구문서", "참고자료"]
                current_role_label = (
                    "참고자료" if _document_role_of(doc) == ROLE_REFERENCE else "연구문서"
                )
                new_role_label = st.radio(
                    "문서 역할",
                    role_options,
                    index=role_options.index(current_role_label),
                    horizontal=True,
                    key=f"lib-edit-role-{doc_id}",
                )
                new_doc_type = st.text_input(
                    "Type",
                    value=doc.get("doc_type") or "",
                    key=f"lib-edit-type-{doc_id}",
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
                            document_role=(
                                ROLE_REFERENCE
                                if new_role_label == "참고자료"
                                else ROLE_PROJECT
                            ),
                            doc_type=new_doc_type.strip() or "other",
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
        st.caption("원문/원본은 Preview에서 보세요.")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Project", doc.get("project_id") or "—")
        m2.metric("Type", doc.get("doc_type") or "—")
        m3.metric("Year", str(doc.get("year") or "—"))
        m4.metric("Role", _role_badge(doc).strip("[]"))

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


def _render_original_preview(doc: dict) -> None:
    """Render uploaded original file when possible; otherwise download fallback."""
    from research_memory.engine.document_preview import (
        docx_to_html,
        hwpx_preview_html,
        hwp_text_preview,
        pdf_page_pngs,
        pdf_pages_preview_html,
        preview_kind,
        resolve_document_file,
        safe_download_name,
        table_preview_records,
        text_file_preview,
    )

    path = resolve_document_file(doc)
    kind = preview_kind(
        path,
        file_type=str(doc.get("file_type") or ""),
        filename=str(doc.get("filename") or ""),
    )
    dl_name = safe_download_name(doc, path)

    if path is None:
        st.warning(
            "원본 파일을 찾지 못했습니다. "
            "`추출 텍스트` 탭에서 평문을 보거나, 파일을 다시 업로드해 주세요."
        )
        return

    meta_cols = st.columns([3, 1])
    meta_cols[0].caption(
        f"원본: `{path.name}` · {kind} · {path.stat().st_size:,} bytes"
    )
    meta_cols[1].download_button(
        "원본 다운로드",
        data=path.read_bytes(),
        file_name=dl_name,
        use_container_width=True,
        key=f"lib-orig-dl-{doc.get('id')}",
    )

    if kind == "pdf":
        # Edge blocks data:/PDF iframes; render pages as images in a scroll box.
        import html as html_lib

        pages, total, err = pdf_page_pngs(path, max_pages=20, scale=1.15)
        if err:
            st.warning(err)
            st.caption("원본 다운로드 또는 `추출 텍스트`로 확인해 주세요.")
            return
        if total > len(pages):
            footer = (
                f"미리보기: 앞 {len(pages)} / 전체 {total}페이지 · "
                "전체는 원본 다운로드로 확인하세요."
            )
        else:
            footer = f"미리보기: {total}페이지"
        box_h = 620
        components.html(
            pdf_pages_preview_html(
                pages,
                max_width_px=680,
                box_height_px=box_h,
                attach_external_footer=True,
            ),
            height=box_h + 4,
            scrolling=False,
        )
        # Footer outside components.html — Streamlit iframes clip in-frame footers.
        st.markdown(
            "<div style='margin-top:-14px;padding:8px 12px 10px;text-align:right;"
            "border:1px solid #d1d5db;border-top:1px solid #e5e7eb;"
            "border-radius:0 0 10px 10px;background:#f9fafb;'>"
            f"<span style='font-size:12px;color:#6b7280;line-height:1.4;'>"
            f"{html_lib.escape(footer)}</span></div>",
            unsafe_allow_html=True,
        )
        return

    if kind == "docx":
        html_body, err = docx_to_html(path)
        if err:
            st.warning(err)
            return
        components.html(html_body, height=740, scrolling=True)
        return

    if kind == "text":
        text, err = text_file_preview(path)
        if err:
            st.warning(err)
            return
        if path.suffix.lower() in {".md", ".markdown"}:
            st.markdown(text)
        else:
            st.text(text)
        return

    if kind == "table":
        rows, err = table_preview_records(path)
        if err:
            st.warning(err)
            return
        st.dataframe(rows, use_container_width=True)
        st.caption("상위 행만 미리봅니다.")
        return

    if kind == "hwp":
        text, err = hwp_text_preview(path)
        if err:
            st.warning(err)
            st.caption("원본 다운로드 또는 `추출 텍스트` 탭에서 확인해 주세요.")
            return
        st.caption("HWP 본문 텍스트 미리보기 · 레이아웃/표는 원본과 다를 수 있습니다.")
        st.text(text)
        return

    if kind == "hwpx":
        import html as html_lib

        html_body, warn, err = hwpx_preview_html(path)
        if err:
            st.info(
                "HWPX 앱 내 미리보기를 만들지 못했습니다. "
                "원본 다운로드 후 한글에서 열어 확인해 주세요."
            )
            st.caption(err)
            st.caption("검색·Chat용 추출 텍스트는 `추출 텍스트` 탭에서 볼 수 있습니다.")
            return
        box_h = 620
        components.html(html_body, height=box_h, scrolling=True)
        footer = warn or "한글 레이아웃 근사 미리보기 · 원본과 다를 수 있음"
        st.markdown(
            "<div style='margin-top:-6px;padding:8px 12px 10px;text-align:right;"
            "border:1px solid #d1d5db;border-radius:0 0 10px 10px;background:#f9fafb;'>"
            f"<span style='font-size:12px;color:#6b7280;line-height:1.4;'>"
            f"{html_lib.escape(footer)}</span></div>",
            unsafe_allow_html=True,
        )
        return

    st.info(
        f"`{path.suffix or kind}` 형식은 원본 화면 미리보기를 아직 지원하지 않습니다. "
        "원본 다운로드로 확인해 주세요."
    )


def _document_insight_card(doc: dict) -> None:
    """Compact Document Insight MVP card above Preview/Summary tabs."""
    from research_memory.engine.document_insight import (
        doc_type_label,
        get_document_insight,
    )

    insight = get_document_insight(doc)
    st.markdown("#### Document Insight")
    if insight:
        dtype = str(insight.get("document_type") or doc.get("doc_type") or "other")
        st.markdown(f"**문서 유형:** {doc_type_label(dtype)} (`{dtype}`)")
        st.markdown(f"**한 줄 요약:** {insight.get('summary') or '—'}")
        topics = insight.get("key_topics") or []
        if topics:
            st.markdown("**주요 주제:** " + " · ".join(str(t) for t in topics))
        uses = insight.get("recommended_uses") or []
        if uses:
            st.markdown("**활용 가능 기능:** " + " · ".join(str(u) for u in uses))
        if st.button("다시 분석", key=f"insight-rerun-{doc['id']}"):
            _run_document_insight(doc, regenerate=True)
        st.markdown("---")
        return

    st.caption("아직 분석 결과가 없습니다. AI가 이 문서의 역할과 Memory 저장 이유를 정리합니다.")
    if st.button("문서 분석 실행", key=f"insight-run-{doc['id']}", type="primary"):
        _run_document_insight(doc, regenerate=False)


def _run_document_insight(doc: dict, *, regenerate: bool) -> None:
    from research_memory.engine.document_insight import generate_document_insight

    with st.spinner("Document Insight 분석 중…"):
        insight = generate_document_insight(
            doc.get("full_text") or "",
            filename=str(doc.get("filename") or ""),
            existing_doc_type=str(doc.get("doc_type") or ""),
        )
    if not insight:
        st.warning("분석을 완료하지 못했습니다. LLM 연결/응답 JSON을 확인해 주세요.")
        return
    try:
        repo.save_document_insight(doc["id"], insight)
        st.success("Document Insight를 저장했습니다.")
        st.rerun()
    except Exception as exc:  # noqa: BLE001
        st.error(f"저장 실패: {exc}")


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
    project_choices = [""] + _rn_project_choices()
    selected = st.selectbox(
        "Project",
        options=project_choices,
        format_func=lambda x: "(선택 안 함)" if x == "" else x,
        key="lib_project_select",
    )
    custom = st.text_input(
        "또는 새 Project ID 직접 입력",
        placeholder="예: 신규과제명",
        key="lib_project_id",
    )
    project_id = (custom or selected or "").strip()
    role_label = st.radio(
        "문서 역할",
        ["연구문서", "참고자료"],
        index=0,
        horizontal=True,
        key="lib_document_role",
        help="연구문서=센터 산출물 · 참고자료=규정/RFP/매뉴얼 등 외부 참고",
    )
    document_role = ROLE_REFERENCE if role_label == "참고자료" else ROLE_PROJECT
    uploads = st.file_uploader(
        "Select files",
        type=["pdf", "docx", "txt", "md", "csv", "xlsx", "xls", "hwpx", "hwp"],
        accept_multiple_files=True,
        key="lib_uploads",
    )

    pending = st.session_state.pop("lib_upload_results", None)
    if pending:
        for item in pending:
            name = item.get("filename") or "file"
            if item.get("ok"):
                if item.get("skipped"):
                    st.info(f"{name}: already in Memory")
                else:
                    st.success(
                        f"{name}: chunks={item.get('chunks')} facts={item.get('facts')} "
                        f"({item.get('role_label') or '연구문서'})"
                    )
            else:
                st.error(f"{name}: {item.get('error', 'failed')}")

    if st.button("Upload into Memory", type="primary", disabled=not uploads, key="lib_ingest"):
        results: list[dict] = []
        for f in uploads or []:
            with st.spinner(f"Ingesting {f.name}…"):
                result = ingest_bytes(
                    f.getvalue(),
                    f.name,
                    repo=repo,
                    project_id=project_id.strip(),
                    document_role=document_role,
                )
            row = {
                "filename": f.name,
                "ok": bool(result.get("ok")),
                "skipped": bool(result.get("skipped")),
                "chunks": result.get("chunks"),
                "facts": result.get("facts"),
                "error": result.get("error", "failed"),
                "role_label": role_label,
            }
            results.append(row)
            if result.get("ok") and project_id and not result.get("skipped"):
                repo.upsert_project(project_id=project_id, title=project_id)
        st.session_state.lib_upload_results = results
        st.rerun()


def _chat_page() -> None:
    st.title("Research Chat")

    docs = [d for d in repo.list_documents() if d.get("status") == "ready"]
    if not docs:
        st.warning("Your Knowledge Base is empty.")
        st.write(
            "Upload documents on Home, then come back to ask with evidence."
        )
        c1, c2 = st.columns(2)
        if c1.button("Go to Home · Upload", type="primary", use_container_width=True):
            _go(PAGE_HOME, focus_upload=True)
        if c2.button("Go to Home", use_container_width=True):
            _go(PAGE_HOME)
        return

    if "messages" not in st.session_state:
        st.session_state.messages = []

    focus_id = st.session_state.get("chat_focus_document_id")
    focus_doc = repo.get_document(str(focus_id)) if focus_id else None
    if focus_doc:
        focus_name = focus_doc.get("title") or focus_doc.get("filename") or focus_id
        fc1, fc2 = st.columns([4, 1])
        fc1.info(f"Focused document: `{focus_name}` — answers use this file only.")
        if fc2.button("Clear focus", use_container_width=True):
            st.session_state.pop("chat_focus_document_id", None)
            st.rerun()

    prefill = st.session_state.get("chat_prefill")
    if prefill:
        st.info(f"From document: {prefill}")
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
            result = answer_question(
                question,
                repo=repo,
                document_id=str(focus_id) if focus_id else None,
            )
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
        # Prefer doc_type from live document when available (for [참고규정]).
        doc = repo.get_document(str(c.get("document_id") or ""))
        badge = _role_badge(
            {
                "document_role": c.get("document_role")
                or (doc or {}).get("document_role"),
                "doc_type": (doc or {}).get("doc_type"),
            }
        )
        filename = escape(str(c.get("filename") or ""))
        location = escape(str(c.get("location") or "—"))
        score = float(c.get("score") or 0)
        snip = escape(str(c.get("snippet") or "")[:240])
        st.markdown(
            f'<div class="rm-card">'
            f"<strong>{i}. {escape(badge)} `{filename}`</strong>"
            f'<div class="rm-meta">{location} · score={score:.3f}</div>'
            f'<div class="rm-snip">{snip}</div>'
            f"</div>",
            unsafe_allow_html=True,
        )

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
    """Project-scoped research/meeting note: sources → AI draft → table → save."""

    for k, v in (
        ("rn_mode", MODE_RESEARCH),
        ("rn_topic", ""),
        ("rn_owner", ""),
        ("rn_author", ""),
        ("rn_content", ""),
        ("rn_results", ""),
        ("rn_etc", ""),
        ("rn_writer_text", ""),
        ("mt_attendees", ""),
        ("mt_agenda", ""),
        ("mt_discussion", ""),
        ("mt_decisions", ""),
        ("mt_actions", ""),
        ("mt_transcript", ""),
        ("mt_recording_name", ""),
    ):
        if k not in st.session_state:
            st.session_state[k] = v
    if "rn_date" not in st.session_state:
        st.session_state.rn_date = date.today()

    mode_label = st.radio(
        "작성 모드",
        [MODE_LABELS[MODE_RESEARCH], MODE_LABELS[MODE_MEETING]],
        horizontal=True,
        key="rn_mode_radio",
        help="연구노트와 회의록은 같은 흐름으로 작성하고, 표 템플릿만 다릅니다.",
    )
    mode = MODE_MEETING if mode_label == MODE_LABELS[MODE_MEETING] else MODE_RESEARCH
    st.session_state.rn_mode = mode
    mode_title = MODE_LABELS[mode]

    # pending convert (widget-safe)
    pending = st.session_state.pop("rn_pending_content", None)
    if pending is not None:
        text = str(pending)
        if mode == MODE_MEETING:
            parsed = parse_meeting_note_fields(text)
            if parsed.get("topic"):
                st.session_state.rn_topic = parsed["topic"]
            if parsed.get("attendees"):
                st.session_state.mt_attendees = parsed["attendees"]
            if parsed.get("agenda"):
                st.session_state.mt_agenda = parsed["agenda"]
            if parsed.get("discussion"):
                st.session_state.mt_discussion = parsed["discussion"]
            if parsed.get("decisions"):
                st.session_state.mt_decisions = parsed["decisions"]
            if parsed.get("actions"):
                st.session_state.mt_actions = parsed["actions"]
            if not any(
                str(st.session_state.get(k) or "").strip()
                for k in ("mt_discussion", "mt_decisions", "mt_actions", "rn_topic")
            ):
                st.session_state.mt_discussion = text
        else:
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
    project_id = (custom or "").strip() or ("" if choice == "(선택)" else choice)
    if not project_id:
        st.info(f"먼저 이 {mode_title}가 속할 **Project**를 선택하거나 입력하세요.")
        return
    st.session_state.rn_project_id = project_id
    st.caption(f"현재 프로젝트: `{project_id}` · 모드: {mode_title}")

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
            help="같은 과제에서 이어갈 과거 노트·보고서·회의록 등",
        )
        if not proj_docs:
            st.caption("이 프로젝트에 아직 Memory 문서가 없습니다.")

    with scol2:
        st.markdown("**New / updated files**")
        uploads = st.file_uploader(
            "새 자료 업로드",
            type=["pdf", "docx", "txt", "md", "csv", "xlsx", "xls", "hwpx", "hwp"],
            accept_multiple_files=True,
            key="rn_uploads",
            help="문서·메모 등 추가 자료",
        )
        paste = st.text_area(
            "짧은 메모 / 붙여넣기",
            height=90,
            key="rn_paste",
            placeholder="오늘 진행 메모…" if mode == MODE_RESEARCH else "회의 메모…",
        )

        if mode == MODE_MEETING:
            st.markdown("**녹음 기반 (회의록)**")
            if stt_available():
                st.caption("자동 받아쓰기: ON (faster-whisper) · 첫 변환 시 모델 다운로드가 있을 수 있습니다.")
            else:
                st.caption(
                    "자동 받아쓰기: OFF — `pip install faster-whisper` 후 앱 재시작, "
                    "또는 트랜스크립트를 붙여넣으세요."
                )
            audio = st.file_uploader(
                "회의 녹음 파일",
                type=["mp3", "wav", "m4a", "webm", "ogg", "flac"],
                accept_multiple_files=False,
                key="rn_audio",
                help="녹음 → 받아쓰기(가능하면) 또는 트랜스크립트 붙여넣기 → 회의록 초안",
            )
            # Apply STT result before the widget is created (Streamlit forbids
            # mutating a widget key after instantiation).
            pending = st.session_state.pop("mt_transcript_pending", None)
            if pending is not None:
                st.session_state.mt_transcript = pending
            st.text_area(
                "트랜스크립트 (받아쓰기 결과 / 직접 붙여넣기)",
                height=140,
                key="mt_transcript",
                placeholder="녹음 받아쓰기 텍스트를 붙여넣거나, 아래 버튼으로 자동 변환을 시도하세요.",
            )
            if audio is not None:
                st.session_state.mt_recording_name = audio.name
                st.session_state.mt_recording_bytes = audio.getvalue()
                if st.button("녹음 → 텍스트 변환 시도", key="rn_transcribe"):
                    with st.spinner("음성 변환 중… (첫 실행은 모델 다운로드로 시간이 걸릴 수 있습니다)"):
                        text, err = transcribe_audio_bytes(audio.getvalue(), audio.name)
                    if text:
                        st.session_state.mt_transcript_pending = text
                        st.success(f"변환됨 · `{audio.name}`")
                        st.rerun()
                    else:
                        st.warning(err or "변환 실패")
            elif st.session_state.get("mt_recording_name"):
                st.caption(f"최근 녹음: `{st.session_state.mt_recording_name}`")

    gen_label = f"{mode_title} 통합 요약 생성"
    if st.button(gen_label, type="primary", key="rn_gen"):
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
        transcript = (st.session_state.get("mt_transcript") or "").strip()
        if mode == MODE_MEETING and transcript:
            rec = st.session_state.get("mt_recording_name") or "recording"
            parts.append(f"# [Transcript] {rec}\n{transcript[:12000]}")
            filenames.append(str(rec))
        if not parts:
            if mode == MODE_MEETING:
                st.warning("녹음 트랜스크립트, Memory 문서, 또는 새 파일을 하나 이상 넣어 주세요.")
            else:
                st.warning("과거 Memory 문서나 새 파일을 하나 이상 넣어 주세요.")
        else:
            with st.spinner(f"{mode_title} 요약 생성 중…"):
                summary = generate_research_note_summary(
                    "\n\n".join(parts),
                    filenames=filenames,
                    mode=mode,
                )
            st.session_state.rn_writer_text = summary
            st.session_state.rn_source_files = filenames
            if mode == MODE_MEETING:
                parsed = parse_meeting_note_fields(summary)
                if parsed.get("topic"):
                    st.session_state.rn_topic = parsed["topic"]
                if parsed.get("attendees"):
                    st.session_state.mt_attendees = parsed["attendees"]
                if parsed.get("agenda"):
                    st.session_state.mt_agenda = parsed["agenda"]
                if parsed.get("discussion"):
                    st.session_state.mt_discussion = parsed["discussion"]
                if parsed.get("decisions"):
                    st.session_state.mt_decisions = parsed["decisions"]
                if parsed.get("actions"):
                    st.session_state.mt_actions = parsed["actions"]
            else:
                parsed = parse_research_note_fields(summary)
                if parsed.get("topic"):
                    st.session_state.rn_topic = parsed["topic"]
                if parsed.get("content"):
                    st.session_state.rn_content = parsed["content"]
                if parsed.get("results"):
                    st.session_state.rn_results = parsed["results"]
            st.session_state.rn_converted = True
            st.rerun()

    summary = st.session_state.get("rn_writer_text") or ""
    draft_ready = bool(summary) or (
        any(
            str(st.session_state.get(k) or "").strip()
            for k in (
                ("rn_topic", "mt_discussion", "mt_decisions", "mt_actions")
                if mode == MODE_MEETING
                else ("rn_topic", "rn_content", "rn_results")
            )
        )
    )
    if not draft_ready:
        if mode == MODE_MEETING:
            st.info(
                "1. Project 선택\n"
                "2. **녹음 파일 + 트랜스크립트**(또는 Memory/메모)\n"
                "3. **통합 요약 생성** → 표 편집 → 다운로드 / Memory 저장\n\n"
                "자동 받아쓰기는 faster-whisper로 동작합니다. "
                "첫 변환은 모델 다운로드로 시간이 걸릴 수 있고, "
                "실패 시 트랜스크립트를 붙여넣으면 됩니다."
            )
        else:
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
        st.markdown(f"**{mode_title} 미리보기 (표)**")
        components.html(_rn_preview_html(mode=mode), height=420, scrolling=True)

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
        st.markdown(f"**{mode_title} 표**")
        if mode == MODE_MEETING:
            st.text_input("회의 제목", key="rn_topic")
            st.date_input("일시", key="rn_date")
            st.text_input("참석자", key="mt_attendees", placeholder="홍길동, 김철수…")
            st.text_area("안건", key="mt_agenda", height=70)
            st.text_area("논의내용", key="mt_discussion", height=120)
            st.text_area("결정사항", key="mt_decisions", height=90)
            st.text_area(
                "Action Item",
                key="mt_actions",
                height=90,
                placeholder="담당 / 기한 / 할 일",
            )
        else:
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
    if mode == MODE_MEETING:
        rows = meeting_rows(
            topic=st.session_state.get("rn_topic") or "",
            date_s=date_s,
            attendees=st.session_state.get("mt_attendees") or "",
            agenda=st.session_state.get("mt_agenda") or "",
            discussion=st.session_state.get("mt_discussion") or "",
            decisions=st.session_state.get("mt_decisions") or "",
            actions=st.session_state.get("mt_actions") or "",
        )
        export_title = "회의록"
        prefix = "meeting_minutes"
    else:
        rows = note_rows(
            topic=st.session_state.get("rn_topic") or "",
            owner=st.session_state.get("rn_owner") or "",
            date_s=date_s,
            author=st.session_state.get("rn_author") or "",
            content=st.session_state.get("rn_content") or "",
            results=st.session_state.get("rn_results") or "",
            etc=st.session_state.get("rn_etc") or "",
        )
        export_title = "연구노트"
        prefix = "research_note"

    try:
        docx_bytes = build_research_note_docx(rows, title=export_title)
        hwpx_bytes = None
        hwpx_err = ""
        if hwpx_available():
            try:
                hwpx_bytes = build_research_note_hwpx(rows, title=export_title)
            except Exception as exc:  # noqa: BLE001
                hwpx_err = str(exc)
    except Exception as exc:  # noqa: BLE001
        st.error(f"표 형식 파일 생성 실패: {exc}")
        return

    topic_slug = re.sub(
        r"[^\w가-힣\-]+", "_", (st.session_state.get("rn_topic") or prefix)
    )[:40]
    base_name = f"{prefix}_{project_id}_{date_s}_{topic_slug}".strip("_")

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
            if mode == MODE_MEETING:
                md = meeting_as_markdown(
                    topic=st.session_state.get("rn_topic") or "",
                    date_s=date_s,
                    attendees=st.session_state.get("mt_attendees") or "",
                    agenda=st.session_state.get("mt_agenda") or "",
                    discussion=st.session_state.get("mt_discussion") or "",
                    decisions=st.session_state.get("mt_decisions") or "",
                    actions=st.session_state.get("mt_actions") or "",
                    project_id=project_id,
                    recording_name=st.session_state.get("mt_recording_name") or "",
                )
            else:
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
            with st.spinner(f"Memory에 {mode_title} 저장 중…"):
                result = ingest_bytes(
                    md.encode("utf-8"),
                    f"{base_name}.md",
                    repo=repo,
                    project_id=project_id,
                )
                ingest_bytes(
                    docx_bytes,
                    f"{base_name}.docx",
                    repo=repo,
                    project_id=project_id,
                )
                # Keep recording next to Memory when provided (optional attachment).
                rec_bytes = st.session_state.get("mt_recording_bytes")
                rec_name = st.session_state.get("mt_recording_name") or "recording.wav"
                if mode == MODE_MEETING and rec_bytes:
                    try:
                        from research_memory.config import RAW_DIR

                        raw_name = f"{base_name}__{Path(str(rec_name)).name}"
                        (RAW_DIR / raw_name).write_bytes(rec_bytes)
                    except Exception:
                        pass
            if result.get("ok"):
                st.success(
                    f"저장됨 · {mode_title} · project=`{project_id}` · "
                    f"{'updated' if result.get('skipped') else 'new'} "
                    f"{result.get('filename')}"
                )
            else:
                st.error(result.get("error") or "저장 실패")

    if hwpx_err:
        st.caption(f"HWPX 생성 불가 — DOCX/Memory 저장은 가능합니다. ({hwpx_err[:160]})")
    st.caption("다운로드·저장은 미리보기와 같은 **표 형식**입니다.")


def _rn_preview_html(*, mode: str = MODE_RESEARCH) -> str:
    d = st.session_state.get("rn_date")
    date_s = d.isoformat() if hasattr(d, "isoformat") else str(d or "")
    if mode == MODE_MEETING:
        rows = meeting_rows(
            topic=st.session_state.get("rn_topic") or "",
            date_s=date_s,
            attendees=st.session_state.get("mt_attendees") or "",
            agenda=st.session_state.get("mt_agenda") or "",
            discussion=st.session_state.get("mt_discussion") or "",
            decisions=st.session_state.get("mt_decisions") or "",
            actions=st.session_state.get("mt_actions") or "",
        )
        title = "회의록"
        tall = {"논의내용", "결정사항", "Action Item", "안 건"}
    else:
        rows = note_rows(
            topic=st.session_state.get("rn_topic") or "",
            owner=st.session_state.get("rn_owner") or "",
            date_s=date_s,
            author=st.session_state.get("rn_author") or "",
            content=st.session_state.get("rn_content") or "",
            results=st.session_state.get("rn_results") or "",
            etc=st.session_state.get("rn_etc") or "",
        )
        title = "연구노트"
        tall = {"내 용", "연구결과", "기타내용"}
    body = []
    for label, val in rows:
        if label in {"내 용", "논의내용"}:
            min_h = "140px"
        elif label in tall:
            min_h = "90px"
        else:
            min_h = "36px"
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
  <div style="text-align:center;font-size:22px;font-weight:700;margin:8px 0 14px;">{escape(title)}</div>
  <table style="width:100%;border-collapse:collapse;table-layout:fixed;">
    {''.join(body)}
  </table>
</div>
"""


def _similarity_panel() -> None:
    st.subheader("문서 유사도 비교")
    st.caption(
        "Doc_Similarity 엔진: MiniLM 문장·페이지 임베딩 + PDF/DOCX/PPTX/HWP/HWPX 파서 + 이미지 pHash. "
        "첫 실행 시 임베딩 모델 다운로드가 필요할 수 있습니다. Memory 비교는 stored_path 원본이 필요합니다."
    )
    mode = st.radio(
        "비교 방식",
        [
            "업로드 ↔ Memory",
            "Memory 문서 ↔ Memory 문서",
            "업로드 ↔ 업로드",
        ],
        horizontal=True,
        key="sim_mode",
    )
    c1, c2, c3 = st.columns(3)
    threshold = c1.slider("문장 유사도 임계값", 0.50, 1.00, 0.85, 0.01, key="sim_threshold")
    page_threshold = c2.slider("페이지 유사도 임계값", 0.50, 1.00, 0.72, 0.01, key="sim_page_threshold")
    top_n = int(
        c3.number_input("표에 표시할 상위 수", min_value=5, max_value=200, value=50, step=5, key="sim_top_n")
    )
    enable_images = st.checkbox("이미지 유사도 포함", value=True, key="sim_enable_images")
    phash_distance = 8
    if enable_images:
        phash_distance = int(
            st.slider("이미지 pHash 거리 상한", 0, 16, 8, 1, key="sim_phash")
        )

    run_kwargs = {
        "threshold": threshold,
        "page_threshold": page_threshold,
        "enable_images": enable_images,
        "phash_distance": phash_distance,
    }
    upload_types = ["pdf", "docx", "pptx", "txt", "md", "csv", "hwpx", "hwp"]

    if mode == "업로드 ↔ Memory":
        project_meta = {
            (p.get("project_id") or "").strip(): p
            for p in repo.list_projects()
            if (p.get("project_id") or "").strip()
        }
        project_choices = [""] + _rn_project_choices()

        def _sim_project_label(pid: str) -> str:
            if not pid:
                return "(전체 Memory)"
            title = (project_meta.get(pid) or {}).get("title") or ""
            title = str(title).strip()
            if title and title != pid:
                return f"{pid} · {title}"
            return pid

        project_filter = st.selectbox(
            "Memory 프로젝트 필터",
            options=project_choices,
            format_func=_sim_project_label,
            key="sim_project_select",
            help="선택한 과제의 ready 문서만 비교합니다. 원본 파일이 있는 문서만 포함됩니다.",
        )
        upload = st.file_uploader(
            "비교할 새 문서",
            type=upload_types,
            key="sim_upload_kb",
        )
        if st.button("유사도 실행", type="primary", key="sim_run_kb", disabled=not upload):
            with st.spinner("Doc_Similarity 분석 중 (파싱·임베딩·이미지)…"):
                result = compare_upload_vs_kb(
                    upload.getvalue(),
                    upload.name,
                    repo=repo,
                    project_id=(project_filter or "").strip() or None,
                    **run_kwargs,
                )
            st.session_state["sim_last_result"] = result

    elif mode == "Memory 문서 ↔ Memory 문서":
        docs = [d for d in repo.list_documents() if d.get("status") == "ready"]
        if len(docs) < 2:
            st.info("Memory에 ready 문서가 2개 이상 필요합니다.")
            return
        labels = {
            f"{d.get('project_id') or '—'} · {d.get('title') or d['filename']}": d["id"]
            for d in docs
        }
        ca, cb = st.columns(2)
        a = ca.selectbox("문서 A", list(labels.keys()), key="sim_doc_a")
        b = cb.selectbox("문서 B", list(labels.keys()), key="sim_doc_b")
        if st.button("유사도 실행", type="primary", key="sim_run_pair"):
            if labels[a] == labels[b]:
                st.warning("서로 다른 문서를 선택하세요.")
            else:
                with st.spinner("Doc_Similarity 분석 중…"):
                    result = compare_kb_documents(
                        labels[a],
                        labels[b],
                        repo=repo,
                        **run_kwargs,
                    )
                st.session_state["sim_last_result"] = result

    else:
        uploads = st.file_uploader(
            "비교할 문서 2개 이상",
            type=upload_types,
            accept_multiple_files=True,
            key="sim_uploads",
        )
        if st.button(
            "유사도 실행",
            type="primary",
            key="sim_run_uploads",
            disabled=not uploads or len(uploads) < 2,
        ):
            with st.spinner("Doc_Similarity 분석 중…"):
                result = compare_uploads(
                    [(f.getvalue(), f.name) for f in uploads],
                    **run_kwargs,
                )
            st.session_state["sim_last_result"] = result

    last = st.session_state.get("sim_last_result")
    if last:
        _render_similarity_result(last, top_n=top_n)


def _sim_verdict_label(verdict: str) -> str:
    raw = str(verdict or "")
    mapped = {
        "exact": "일치",
        "high": "높음",
        "medium": "보통",
        "low": "낮음",
        "동일 문장": "일치",
        "동일 이미지": "일치",
        "매우 유사": "높음",
        "유사 가능성": "보통",
    }.get(raw)
    return mapped or raw


def _render_similarity_result(result: dict, *, top_n: int = 50) -> None:
    if not result.get("ok"):
        st.error(result.get("error") or "유사도 비교에 실패했습니다.")
        return
    if result.get("error"):
        st.warning(f"부분 경고: {result['error']}")

    stats = result.get("stats") or {}
    sent_stats = stats.get("sentence") or stats
    page_stats = stats.get("page") or {}
    img_stats = stats.get("image") or {}
    overlap = result.get("overlap_stats") or {}

    meta = []
    if result.get("query_file"):
        meta.append(f"질의 파일: `{result['query_file']}`")
    if result.get("file_a") and result.get("file_b"):
        meta.append(f"`{result['file_a']}` ↔ `{result['file_b']}`")
    names = result.get("file_names") or []
    if names:
        meta.append(f"파일 {len(names)}개")
    if meta:
        st.caption(" · ".join(meta))

    sentence_pairs = list(result.get("sentence_pairs") or result.get("pairs") or [])
    page_pairs = list(result.get("page_pairs") or [])
    image_pairs = list(result.get("image_pairs") or [])
    matched_pngs = list(result.get("matched_page_pngs") or [])
    logs = result.get("log_entries") or []
    n = max(1, int(top_n))

    tab_s, tab_i, tab_p, tab_sum = st.tabs(
        ["유사 문장", "유사 이미지", "유사 페이지", "분석 요약"]
    )

    with tab_s:
        if not sentence_pairs:
            st.info("임계값 이상의 유사 문장 쌍이 없습니다.")
        else:
            show = sentence_pairs[:n]
            st.caption(f"상위 {len(show)} / 전체 {len(sentence_pairs)}개")
            st.dataframe(
                [
                    {
                        "점수": round(float(p.get("score") or p.get("similarity") or 0), 3),
                        "판정": _sim_verdict_label(p.get("verdict")),
                        "파일 A": p.get("file_a"),
                        "위치 A": p.get("location_a"),
                        "문장 A": str(p.get("text_a") or "")[:220],
                        "파일 B": p.get("file_b"),
                        "위치 B": p.get("location_b"),
                        "문장 B": str(p.get("text_b") or "")[:220],
                    }
                    for p in show
                ],
                use_container_width=True,
                hide_index=True,
            )

    with tab_i:
        if not image_pairs:
            st.info(
                "유사 이미지 쌍이 없습니다. PDF/DOCX 등에서 이미지가 추출됐는지 "
                "분석 요약의 처리 로그에서 이미지 개수를 확인하세요."
            )
        else:
            show = image_pairs[:n]
            st.caption(f"상위 {len(show)} / 전체 {len(image_pairs)}개")
            st.dataframe(
                [
                    {
                        "pHash거리": p.get("phash_distance"),
                        "판정": _sim_verdict_label(p.get("verdict")),
                        "파일 A": p.get("file_a"),
                        "위치 A": p.get("location_a"),
                        "파일 B": p.get("file_b"),
                        "위치 B": p.get("location_b"),
                    }
                    for p in show
                ],
                use_container_width=True,
                hide_index=True,
            )
            with st.expander("이미지 미리보기", expanded=True):
                for i, p in enumerate(show[:12], start=1):
                    st.markdown(
                        f"**[{i}] {_sim_verdict_label(p.get('verdict'))} · "
                        f"거리 {p.get('phash_distance')}** — "
                        f"`{p.get('file_a')}` / {p.get('location_a')} ↔ "
                        f"`{p.get('file_b')}` / {p.get('location_b')}"
                    )
                    ca, cb = st.columns(2)
                    if p.get("image_bytes_a"):
                        ca.image(p["image_bytes_a"], use_container_width=True)
                    if p.get("image_bytes_b"):
                        cb.image(p["image_bytes_b"], use_container_width=True)

    with tab_p:
        # 유사 페이지 표 + 페이지 PNG를 한 탭에 (이전 Doc_Similarity와 같이)
        st.markdown("#### 유사 페이지 (텍스트)")
        if not page_pairs:
            st.info("임계값 이상의 유사 페이지 쌍이 없습니다.")
        else:
            show = page_pairs[:n]
            st.caption(f"상위 {len(show)} / 전체 {len(page_pairs)}개")
            st.dataframe(
                [
                    {
                        "점수": round(float(p.get("score") or p.get("similarity") or 0), 3),
                        "판정": _sim_verdict_label(p.get("verdict")),
                        "파일 A": p.get("file_a"),
                        "페이지 A": p.get("page_a") if p.get("page_a") is not None else p.get("location_a"),
                        "미리보기 A": str(p.get("text_a") or "")[:180],
                        "파일 B": p.get("file_b"),
                        "페이지 B": p.get("page_b") if p.get("page_b") is not None else p.get("location_b"),
                        "미리보기 B": str(p.get("text_b") or "")[:180],
                    }
                    for p in show
                ],
                use_container_width=True,
                hide_index=True,
            )
            with st.expander("페이지 텍스트 나란히 비교", expanded=False):
                for i, p in enumerate(show[:15], start=1):
                    st.markdown(
                        f"**[{i}] {_sim_verdict_label(p.get('verdict'))} · "
                        f"{float(p.get('score') or p.get('similarity') or 0):.3f}** — "
                        f"`{p.get('file_a')}` / {p.get('location_a')} ↔ "
                        f"`{p.get('file_b')}` / {p.get('location_b')}"
                    )
                    c1, c2 = st.columns(2)
                    with c1:
                        st.write(str(p.get("text_a") or "")[:1200])
                    with c2:
                        st.write(str(p.get("text_b") or "")[:1200])
                    if i < min(15, len(show)):
                        st.divider()

        st.divider()
        st.markdown("#### 페이지 PNG (유사 문장 근거 하이라이트)")
        st.caption("PDF만 렌더됩니다. 노란색 = 이 페이지 쌍을 만든 유사 문장 위치.")
        if not matched_pngs:
            st.info("유사 문장 기반 페이지 PNG가 없습니다.")
        else:
            from collections import OrderedDict

            combined = [s for s in matched_pngs if s.get("side") == "AB"]
            pairs: OrderedDict = OrderedDict()
            if combined:
                for s in combined:
                    label = s.get("pair_label") or f"{s.get('file_a')} = {s.get('file_b')}"
                    pairs[label] = s
            else:
                sides_map: OrderedDict = OrderedDict()
                for s in matched_pngs:
                    label = s.get("pair_label") or f"{s.get('file_name')} p.{s.get('page_number')}"
                    sides_map.setdefault(label, {"A": None, "B": None, "meta": s})
                    sides_map[label][s.get("side", "A")] = s
                for label, sides in sides_map.items():
                    pairs[label] = sides

            st.caption(f"매칭 페이지 쌍 {len(pairs)}개")
            meta_rows = []
            for label, item in pairs.items():
                if isinstance(item, dict) and item.get("side") == "AB":
                    meta_rows.append(
                        {
                            "비교": label,
                            "파일 A": item.get("file_a", ""),
                            "페이지 A": item.get("page_a", ""),
                            "파일 B": item.get("file_b", ""),
                            "페이지 B": item.get("page_b", ""),
                            "유사문장쌍": item.get("pair_count", ""),
                        }
                    )
                else:
                    meta = item.get("meta", item) if isinstance(item, dict) else {}
                    meta_rows.append(
                        {
                            "비교": label,
                            "파일 A": meta.get("file_a", ""),
                            "페이지 A": meta.get("page_a", ""),
                            "파일 B": meta.get("file_b", ""),
                            "페이지 B": meta.get("page_b", ""),
                            "유사문장쌍": meta.get("pair_count", ""),
                        }
                    )
            if meta_rows:
                st.dataframe(meta_rows, use_container_width=True, hide_index=True)

            st.markdown("##### 나란히 미리보기")
            for i, (label, item) in enumerate(list(pairs.items())[:30], start=1):
                with st.expander(str(label), expanded=(i == 1)):
                    if isinstance(item, dict) and item.get("side") == "AB":
                        st.markdown(
                            f"**A:** {item.get('file_a')} · p.{item.get('page_a')}  |  "
                            f"**B:** {item.get('file_b')} · p.{item.get('page_b')}"
                        )
                        if item.get("png_bytes"):
                            st.image(item["png_bytes"], use_container_width=True)
                    elif isinstance(item, dict):
                        a, b = item.get("A") or {}, item.get("B") or {}
                        ca, cb = st.columns(2)
                        with ca:
                            if a:
                                st.caption(f"A · {a.get('file_name')} p.{a.get('page_number')}")
                                if a.get("png_bytes"):
                                    st.image(a["png_bytes"], use_container_width=True)
                        with cb:
                            if b:
                                st.caption(f"B · {b.get('file_name')} p.{b.get('page_number')}")
                                if b.get("png_bytes"):
                                    st.image(b["png_bytes"], use_container_width=True)

    with tab_sum:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("유사 문장 쌍", int(sent_stats.get("pair_count") or 0))
        m2.metric("유사 페이지 쌍", int(page_stats.get("pair_count") or 0))
        m3.metric("유사 이미지 쌍", int(img_stats.get("pair_count") or 0))
        m4.metric("페이지 PNG", len(matched_pngs))
        st.caption(
            f"추출 — 문장 {result.get('query_units', len(result.get('sentences') or []))} · "
            f"페이지 {result.get('page_units', len(result.get('pages') or []))} · "
            f"이미지 {result.get('image_units', len(result.get('images') or []))}"
        )
        if overlap:
            st.caption(
                f"문장 겹침 {overlap.get('overlapped_sentences', 0)} / "
                f"{overlap.get('total_sentences', 0)} "
                f"({overlap.get('overlap_ratio_pct', 0)}%)"
            )
        if logs:
            st.markdown("#### 처리 로그")
            st.dataframe(logs, use_container_width=True, hide_index=True)
        matrix = result.get("file_matrix")
        if matrix is not None:
            st.markdown("#### 파일 × 파일 유사 문장 쌍")
            try:
                st.dataframe(matrix, use_container_width=True)
            except Exception:  # noqa: BLE001
                st.write(matrix)


def _proposal_panel() -> None:
    st.subheader("RFP → 제안 초안")
    st.caption("RFP와 Memory 근거로 제안 초안을 만듭니다.")
    project_meta = {
        (p.get("project_id") or "").strip(): p
        for p in repo.list_projects()
        if (p.get("project_id") or "").strip()
    }
    project_choices = [""] + _rn_project_choices()

    def _prop_project_label(pid: str) -> str:
        if not pid:
            return "(전체 — 참고규정은 항상 포함)"
        title = (project_meta.get(pid) or {}).get("title") or ""
        title = str(title).strip()
        if title and title != pid:
            return f"{pid} · {title}"
        return pid

    selected_project = st.selectbox(
        "KB Project filter",
        options=project_choices,
        format_func=_prop_project_label,
        key="prop_project_select",
        help="연구문서만 필터됩니다. 참고규정(운영요령 등)은 필터와 무관하게 포함됩니다.",
    )
    project_filter = (selected_project or "").strip() or None
    rfp_file = st.file_uploader(
        "RFP / 공고문",
        type=["pdf", "docx", "txt", "md", "csv", "xlsx", "xls", "hwpx", "hwp"],
        key="prop_rfp_upload",
        help="Library와 동일: PDF/DOCX/TXT/MD/CSV/Excel/HWP/HWPX",
    )
    if st.button("Analyze RFP + Match Memory", type="primary", disabled=not rfp_file):
        with st.spinner("Parsing RFP and retrieving KB evidence…"):
            chunks, err = parse_rfp_bytes(rfp_file.getvalue(), rfp_file.name)
            if err and not chunks:
                st.error(err)
                return
            rfp = analyze_rfp(chunks)
            split = gather_kb_evidence_split(
                rfp,
                repo=repo,
                project_id=project_filter,
            )
            roles = suggest_roles(rfp, split["combined"])
            st.session_state["prop_rfp_result"] = rfp
            st.session_state["prop_research_evidence"] = [
                c.to_dict() for c in split["research"]
            ]
            st.session_state["prop_reference_evidence"] = [
                c.to_dict() for c in split["reference"]
            ]
            st.session_state["prop_evidence"] = [
                c.to_dict() for c in split["combined"]
            ]
            st.session_state["prop_roles"] = roles
            st.session_state.pop("prop_draft", None)
            st.session_state.pop("prop_selected", None)
            st.session_state.pop("prop_review_findings", None)
            st.session_state.pop("prop_draft_v1", None)

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

    research_dicts = st.session_state.get("prop_research_evidence") or []
    reference_dicts = st.session_state.get("prop_reference_evidence") or []
    if not research_dicts and not reference_dicts:
        st.warning("Memory 근거가 없습니다. Library에서 센터 자료를 먼저 넣으세요.")
    else:
        with st.expander(
            f"KB evidence (연구문서 {len(research_dicts)} · 참고규정 {len(reference_dicts)})",
            expanded=False,
        ):
            with st.expander(f"[연구문서] {len(research_dicts)}건", expanded=False):
                if not research_dicts:
                    st.caption(
                        "연구문서 근거 없음 — Project filter 또는 연구문서 업로드를 확인하세요."
                    )
                else:
                    for i, c in enumerate(research_dicts[:8], start=1):
                        doc = repo.get_document(str(c.get("document_id") or ""))
                        badge = _role_badge(
                            {
                                "document_role": c.get("document_role")
                                or (doc or {}).get("document_role"),
                                "doc_type": (doc or {}).get("doc_type"),
                            }
                        )
                        filename = escape(str(c.get("filename") or ""))
                        location = escape(str(c.get("location") or ""))
                        score = float(c.get("score") or 0)
                        snip = escape(str(c.get("snippet") or "")[:240])
                        st.markdown(
                            f'<div class="rm-card">'
                            f"<strong>[{i}] {escape(badge)} {filename}</strong>"
                            f'<div class="rm-meta">{location} · score={score:.3f}</div>'
                            f'<div class="rm-snip">{snip}</div>'
                            f"</div>",
                            unsafe_allow_html=True,
                        )
            with st.expander(f"[참고규정] {len(reference_dicts)}건", expanded=False):
                if not reference_dicts:
                    st.warning(
                        "참고규정 근거가 없습니다. Library의 **Center 자료**에 "
                        "운영요령 등 참고자료(reference)가 있는지 확인하세요."
                    )
                else:
                    for i, c in enumerate(reference_dicts[:8], start=1):
                        doc = repo.get_document(str(c.get("document_id") or ""))
                        badge = _role_badge(
                            {
                                "document_role": c.get("document_role")
                                or (doc or {}).get("document_role"),
                                "doc_type": (doc or {}).get("doc_type"),
                            }
                        )
                        filename = escape(str(c.get("filename") or ""))
                        location = escape(str(c.get("location") or ""))
                        score = float(c.get("score") or 0)
                        snip = escape(str(c.get("snippet") or "")[:240])
                        st.markdown(
                            f'<div class="rm-card">'
                            f"<strong>[{i}] {escape(badge)} {filename}</strong>"
                            f'<div class="rm-meta">{location} · score={score:.3f}</div>'
                            f'<div class="rm-snip">{snip}</div>'
                            f"</div>",
                            unsafe_allow_html=True,
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
        with st.spinner("섹션별 근거 선별 후 초안 생성 중… (시간이 더 걸릴 수 있습니다)"):
            research_cites = _citations_from_dicts(research_dicts)
            reference_cites = _citations_from_dicts(reference_dicts)
            draft = generate_draft(
                rfp,
                selected,
                research_evidence=research_cites,
                reference_evidence=reference_cites,
            )
            st.session_state["prop_draft"] = draft
            st.session_state["prop_selected"] = selected
            st.session_state.pop("prop_review_findings", None)
            st.session_state.pop("prop_draft_v1", None)

    draft = st.session_state.get("prop_draft")
    selected = st.session_state.get("prop_selected") or selected
    if draft and selected:
        st.markdown("### Draft")
        if draft.get("revised_sections"):
            keys = draft.get("revised_sections") or []
            labels = [DRAFT_LABELS_UI.get(k, k) for k in keys]
            st.caption(f"Draft v2 · 수정 섹션: {', '.join(labels) or '-'}")
        for key, label in (
            ("necessity", "참여 필요성"),
            ("center_role", "담당 역할"),
            ("work_details", "수행내용"),
            ("deliverables", "산출물"),
            ("compliance_notes", "운영요령·준수 포인트"),
            ("open_questions", "확인 필요"),
        ):
            st.markdown(f"**{label}**")
            st.write(clean_draft_prose(str(draft.get(key, "") or "")))
            sec_ev = (draft.get("section_evidence") or {}).get(key) or {}
            r_n = len(sec_ev.get("research") or [])
            f_n = len(sec_ev.get("reference") or [])
            if r_n or f_n:
                st.caption(f"근거 {r_n + f_n}건")

        # Budget plan skeleton + optional total/lead-share scenario.
        st.markdown("**다음 단계 연구개발비 사용 계획**")
        st.caption(
            "(단위 : 천원) · 시나리오 배분은 주관/공동(A) 합계만 채움 · 비목 세분은 확인 필요"
        )
        parsed_default = parse_budget_amount_cheon(rfp.get("budget"))
        default_total = int(
            draft.get("budget_scenario_total")
            or parsed_default
            or 0
        )
        default_lead = float(draft.get("budget_scenario_lead_pct") or 100.0)
        b1, b2, b3 = st.columns([2, 1, 1])
        total_in = b1.number_input(
            "총액(천원)",
            min_value=0,
            value=max(0, default_total),
            step=1000,
            key=f"prop_budget_total_{draft.get('mode', 'v1')}",
        )
        lead_in = b2.number_input(
            "주관비중(%)",
            min_value=0.0,
            max_value=100.0,
            value=min(100.0, max(0.0, default_lead)),
            step=5.0,
            key=f"prop_budget_lead_{draft.get('mode', 'v1')}",
        )
        apply = b3.button("시나리오 배분 적용", key="prop_budget_apply", use_container_width=True)
        if apply:
            _md, budget_rows = build_budget_plan_section(
                rfp, total_cheon=int(total_in), lead_pct=float(lead_in)
            )
            draft["budget_plan"] = _md
            draft["budget_plan_table"] = budget_rows
            draft["budget_scenario_total"] = int(total_in)
            draft["budget_scenario_lead_pct"] = float(lead_in)
            st.session_state["prop_draft"] = draft
            st.rerun()

        budget_rows = draft.get("budget_plan_table")
        if not budget_rows:
            _md, budget_rows = build_budget_plan_section(rfp)
            draft["budget_plan"] = _md
            draft["budget_plan_table"] = budget_rows
            st.session_state["prop_draft"] = draft
        if draft.get("budget_scenario_total") is not None:
            lead_a = int(
                round(
                    int(draft["budget_scenario_total"])
                    * float(draft.get("budget_scenario_lead_pct") or 0)
                    / 100.0
                )
            )
            st.caption(
                f"적용됨: 총 {_fmt_budget_ui(int(draft['budget_scenario_total']))}천원 · "
                f"주관 {float(draft.get('budget_scenario_lead_pct') or 0):.1f}% "
                f"({_fmt_budget_ui(lead_a)}) / "
                f"공동(A) {_fmt_budget_ui(int(draft['budget_scenario_total']) - lead_a)}"
            )
        st.dataframe(budget_rows, use_container_width=True, hide_index=True)
        with st.expander("연구비 각주·안내"):
            st.markdown(
                str(draft.get("budget_plan") or "").split("각주(개조식)")[-1]
                if "각주(개조식)" in str(draft.get("budget_plan") or "")
                else str(draft.get("budget_plan") or "")
            )

        st.markdown("### 초안 검토")
        if st.button("초안 검토", key="prop_review_btn"):
            with st.spinner("RFP·연구문서·참고규정 기준으로 초안 검토 중…"):
                findings = review_draft_quality(
                    rfp,
                    draft,
                    research_evidence=_citations_from_dicts(research_dicts),
                    reference_evidence=_citations_from_dicts(reference_dicts),
                    selected_role=selected,
                )
                st.session_state["prop_review_findings"] = findings

        findings = st.session_state.get("prop_review_findings") or []
        if findings:
            _render_proposal_review(findings)
            if st.button(
                "검토 결과 반영하여 Draft 개선",
                type="primary",
                key="prop_revise_btn",
            ):
                with st.spinner("확인 필요 섹션만 재생성 중…"):
                    st.session_state["prop_draft_v1"] = dict(draft)
                    revised = revise_draft_from_review(
                        draft,
                        findings,
                        rfp,
                        selected,
                        research_evidence=_citations_from_dicts(research_dicts),
                        reference_evidence=_citations_from_dicts(reference_dicts),
                    )
                    st.session_state["prop_draft"] = revised
                st.rerun()

        research_cites = _citations_from_dicts(research_dicts)
        reference_cites = _citations_from_dicts(reference_dicts)
        md = build_markdown(
            rfp,
            selected,
            draft,
            research_evidence=research_cites,
            reference_evidence=reference_cites,
        )
        st.download_button("Download Markdown", md, file_name="proposal_draft.md")
        docx_bytes = export_docx_bytes(rfp, selected, draft)
        st.download_button(
            "Download DOCX",
            docx_bytes,
            file_name="proposal_draft.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )


DRAFT_LABELS_UI = {
    "necessity": "참여 필요성",
    "center_role": "담당 역할",
    "work_details": "수행내용",
    "yearly_plan": "연차별 수행계획",
    "deliverables": "산출물",
    "kpi_draft": "KPI 초안",
    "consortium_role": "컨소시엄 내 역할",
    "expected_effects": "기대효과",
    "compliance_notes": "운영요령·준수 포인트",
    "open_questions": "확인 필요",
    "budget_plan": "연구개발비 사용 계획",
}


def _fmt_budget_ui(n: int) -> str:
    return f"{int(n):,}"


def _render_proposal_review(findings: list) -> None:
    """Group review findings by section for a compact UI."""
    by_section: dict[str, list] = {}
    for item in findings:
        if not isinstance(item, dict):
            continue
        sec = str(item.get("section") or "전체").strip() or "전체"
        by_section.setdefault(sec, []).append(item)

    for section, items in by_section.items():
        needs = [x for x in items if str(x.get("status")) == "확인 필요"]
        if needs:
            st.markdown(f"**{section}**  \n⚠ 확인 필요")
            for x in needs:
                msg = str(x.get("message") or "").strip()
                action = str(x.get("suggested_action") or "").strip()
                ev = x.get("evidence") or []
                ev_txt = ", ".join(str(e) for e in ev[:3]) if isinstance(ev, list) else ""
                line = f"- {msg}"
                if action:
                    line += f"  \n  → {action}"
                if ev_txt:
                    line += f"  \n  · 근거: {ev_txt}"
                st.markdown(line)
        else:
            ok = items[0] if items else {}
            msg = str(ok.get("message") or "특이사항 없음").strip()
            st.markdown(f"**{section}**  \n✅ 문제없음  \n- {msg}")


def _schedule_panel() -> None:
    from datetime import date as date_cls

    st.caption("날짜 칸의 빈 곳을 클릭하면 일정을 추가하고, 일정 칩을 클릭하면 상세가 팝업으로 열립니다.")

    with st.expander("과제 등록·수정", expanded=False):
        pid = st.text_input("과제 ID", key="sched_pid")
        title = st.text_input("과제명", key="sched_title")
        owner = st.text_input("담당", key="sched_owner")
        c1, c2 = st.columns(2)
        start = c1.text_input("시작 (YYYY-MM-DD)", key="sched_start")
        end = c2.text_input("종료 (YYYY-MM-DD)", key="sched_end")
        if st.button("과제 저장", key="sched_save_project"):
            if not pid.strip():
                st.warning("과제 ID를 입력하세요.")
            else:
                repo.upsert_project(
                    project_id=pid.strip(),
                    title=title.strip() or pid.strip(),
                    owner=owner.strip(),
                    start_date=start.strip(),
                    end_date=end.strip(),
                )
                st.success(f"저장됨: {pid.strip()}")
                st.rerun()

    projects = repo.list_projects()
    if not projects:
        st.info("등록된 과제가 없습니다. 위에서 과제를 먼저 만드세요.")
        return

    project_map = {p["project_id"]: p for p in projects}

    today = date_cls.today()
    if "sched_year" not in st.session_state:
        st.session_state.sched_year = today.year
    if "sched_month" not in st.session_state:
        st.session_state.sched_month = today.month

    year = int(st.session_state.sched_year)
    month = int(st.session_state.sched_month)
    cal_grid = calendar_grid_sunday(year, month)
    grid_from, grid_to = grid_date_bounds(cal_grid)

    _inject_schedule_calendar_css()

    tool_l, tool_r = st.columns([4, 1])
    with tool_l:
        st.caption("상태")
        status_options = ["(전체)"] + [STATUS_LABELS[s] for s in STATUSES]
        status_choice = st.selectbox(
            "상태 필터", status_options, key="sched_status_filter", label_visibility="collapsed"
        )
        filter_status = None
        if status_choice != "(전체)":
            filter_status = next(
                s for s in STATUSES if STATUS_LABELS[s] == status_choice
            )
    with tool_r:
        if st.button("오늘", key="sched_today", use_container_width=True):
            st.session_state.sched_year = today.year
            st.session_state.sched_month = today.month
            st.session_state.sched_selected_date = today.isoformat()
            st.session_state.pop("sched_selected_item_id", None)
            st.session_state.pop("sched_open_add", None)
            st.rerun()

    nav_l, nav_c, nav_r = st.columns([1, 2, 1])
    with nav_l:
        if st.button("‹", key="sched_prev", use_container_width=True):
            y, m = shift_month(
                st.session_state.sched_year, st.session_state.sched_month, -1
            )
            st.session_state.sched_year, st.session_state.sched_month = y, m
            st.rerun()
    with nav_c:
        st.markdown(
            f'<div class="rm-cal-nav-label">{year}년 {month}월</div>',
            unsafe_allow_html=True,
        )
    with nav_r:
        if st.button("›", key="sched_next", use_container_width=True):
            y, m = shift_month(
                st.session_state.sched_year, st.session_state.sched_month, 1
            )
            st.session_state.sched_year, st.session_state.sched_month = y, m
            st.rerun()

    items = repo.list_schedule_items(
        project_id=None,
        status=filter_status,
        date_from=grid_from,
        date_to=grid_to,
    )
    by_day = items_by_date(items)
    month_items = [it for it in items if item_overlaps_month(it, year, month)]
    items_by_id = {it["id"]: it for it in items}

    selected_item_id = st.session_state.get("sched_selected_item_id")
    selected_date = st.session_state.get("sched_selected_date")

    cal_html = render_calendar_html(
        year=year,
        month=month,
        grid=cal_grid,
        by_day=by_day,
        today=today,
        selected_date=selected_date,
        selected_item_id=selected_item_id,
    )
    mount_schedule_calendar(cal_html)

    selected_item = items_by_id.get(selected_item_id or "")
    if selected_item is None and selected_item_id:
        selected_item = repo.get_schedule_item(selected_item_id)

    if selected_item:
        _schedule_edit_dialog(selected_item, projects, project_map)
    elif st.session_state.get("sched_open_add") and selected_date:
        try:
            add_day = date_cls.fromisoformat(selected_date)
        except ValueError:
            add_day = today
        _schedule_add_dialog(add_day, projects, project_map)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("이번 달 일정", len(month_items))
    m2.metric(
        "회의",
        sum(1 for i in month_items if normalize_event_type(i.get("event_type")) == "meeting"),
    )
    m3.metric(
        "제출",
        sum(1 for i in month_items if normalize_event_type(i.get("event_type")) == "submission"),
    )
    m4.metric(
        "완료",
        sum(1 for i in month_items if normalize_status(i.get("status")) == "done"),
    )


if __name__ == "__main__":
    main()
