# Research Memory Platform

**An Organizational Research Intelligence Platform**

> Memory가 핵심이고, Intelligence는 결과입니다.  
> Phase 1 = Memory MVP: Pipeline → Metadata/Facts → Knowledge Base → Research Chat (citations)

## Goals

1. **Enable evidence-based reuse of organizational research assets**
2. **Preserve and operationalize institutional research knowledge**

Non-goals for Phase 1: Similarity / Proposal / Milestone services, Catena-X/KMX, generic “center ChatGPT”.

## Architecture (Phase 1)

```
Source Documents
    ↓
Document Intelligence Pipeline
    ↓
Metadata / Facts
    ↓
Knowledge Base
    ↓
AI Services (Chat via Research Memory Engine: Retrieval + Generation)
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
```

## Supported ingest formats (Phase 1)

PDF · DOCX · TXT/MD · CSV · XLSX · HWPX (best-effort XML)

Legacy `.hwp` binary is out of Phase 1 scope (reuse HWP_analyst backends in a later phase).

## Layout

```
research_memory/
  pipeline/     # extract → chunk → metadata/facts → ingest
  kb/           # sqlite + tf-idf index
  engine/       # retrieval + chat generation
app.py          # Streamlit UI
demo/           # non-sensitive sample corpus (after seed_demo)
```

## Phase map

| Phase | Focus |
|-------|--------|
| **1 (this)** | Pipeline, Metadata/Facts, KB, Chat+citations |
| 2 | Similarity service |
| 3 | Proposal service |
| 4 | Milestone / Tracking |
| 5 | Ops hardening |
