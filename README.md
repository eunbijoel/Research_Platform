# Research Memory Platform

**An Organizational Research Intelligence Platform**

> Memory가 핵심이고, Intelligence는 결과입니다.  
> Phase 1–3: Pipeline → Metadata/Facts → Knowledge Base → Chat + Similarity + Proposal

## Goals

1. **Enable evidence-based reuse of organizational research assets**
2. **Preserve and operationalize institutional research knowledge**

Non-goals (for now): Proposal / Milestone services, Catena-X/KMX, generic “center ChatGPT”.

## Architecture (Phase 1–3)

```
Source Documents
    ↓
Document Intelligence Pipeline
    ↓
Metadata / Facts
    ↓
Knowledge Base
    ↓
AI Services
  · Chat (Retrieval + Generation, citations required)
  · Similarity (Retrieval + Reasoning: upload↔KB / KB↔KB)
  · Proposal (RFP analyze + KB evidence → center draft)
```

## Location

Canonical project path (data disk):

```text
/mnt/data/eunbi/research-memory
```

Convenience symlink: `/home/eunbi/research-memory` → same directory.

## Quick start

```bash
cd /mnt/data/eunbi/research-memory
# or: cd ~/research-memory

source .venv/bin/activate   # or use .venv/bin/python directly

# seed demo corpus
python -m research_memory.cli seed_demo

# ask without UI
python -m research_memory.cli chat "Research Memory의 핵심 원칙은?"

# similarity: new file vs KB
python -m research_memory.cli similarity ./demo/center_overview.md --project DEMO-2026

# UI
./run_app.sh
```

Browser: http://127.0.0.1:8505

Optional LLM: run Ollama locally and set `RM_MODEL_NAME` (see `.env.example`).  
If Ollama is offline, Chat still returns **extractive** answers with citations.

## CLI

```bash
python -m research_memory.cli ingest ./demo --project DEMO-2026
python -m research_memory.cli list
python -m research_memory.cli chat "센터 역할은 무엇인가?"
python -m research_memory.cli similarity ./demo/proposal_excerpt.md
python -m research_memory.cli similarity --doc-a <id> --doc-b <id>
python -m research_memory.cli proposal ./path/to/rfp.md --project DEMO-2026 --out proposal_draft.md
```

## Supported ingest formats

PDF · DOCX · TXT/MD · CSV · XLSX · HWPX (best-effort XML)

Legacy `.hwp` binary is deferred (reuse HWP_analyst backends later).

## Layout

```
research_memory/
  pipeline/     # extract → chunk → metadata/facts → ingest
  kb/           # sqlite + tf-idf index
  engine/       # retrieval + chat + similarity
app.py          # Streamlit UI
demo/           # non-sensitive sample corpus
```

## Phase map

| Phase | Focus | Status |
|-------|--------|--------|
| **1** | Pipeline, Metadata/Facts, KB, Chat+citations | done |
| **2** | Similarity service (upload↔KB / KB↔KB) | done |
| **3** | Proposal service (RFP + KB → draft) | done |
| 4 | Milestone / Tracking | next |
| 5 | Ops hardening | planned |
