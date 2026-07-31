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
from research_memory.pipeline.ingest import ingest_bytes
from research_memory.schema import Citation

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
        "An Organizational Research Intelligence Platform — "
        "Phase 1–4: Memory · Similarity · Proposal · Milestone"
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
            "**Phase 1–4**\n"
            "- Chat / Similarity / Proposal\n"
            "- Milestone Tracking + gap report"
        )

    tab_chat, tab_sim, tab_prop, tab_mile, tab_ingest, tab_library, tab_facts = st.tabs(
        [
            "Research Chat",
            "Similarity",
            "Proposal",
            "Milestone",
            "Ingest",
            "Library",
            "Facts",
        ]
    )

    with tab_ingest:
        _ingest_tab()
    with tab_library:
        _library_tab()
    with tab_facts:
        _facts_tab()
    with tab_sim:
        _similarity_tab()
    with tab_prop:
        _proposal_tab()
    with tab_mile:
        _milestone_tab()
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


def _similarity_tab() -> None:
    st.subheader("Similarity AI (Retrieval + Reasoning)")
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


def _proposal_tab() -> None:
    st.subheader("Proposal AI (Retrieval + Reasoning + Generation)")
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
        st.warning("Memory 근거가 없습니다. Ingest 탭에서 센터 자료를 먼저 넣으세요.")
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


def _milestone_tab() -> None:
    st.subheader("Milestone AI (Tracking)")
    st.caption(
        "과제·마일스톤을 등록하고, Memory에 적재된 산출물과 대조해 갭/지연을 추적합니다."
    )

    if st.button("Seed DEMO-2026 project + milestones"):
        result = seed_demo_project(repo=repo, project_id="DEMO-2026")
        st.success(result)

    with st.expander("Create / update project", expanded=False):
        pid = st.text_input("Project ID", value="DEMO-2026", key="mile_pid")
        title = st.text_input("Title", value="Research Memory Platform 데모 과제", key="mile_title")
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
            st.write(f"- {d['filename']} · type={d.get('doc_type')} · {d.get('created_at', '')[:10]}")


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
