# Excel Analyzer integration

Research Memory의 **코딩 에이전트** 탭은 `excel_ai_analyzer`를 Excel 전문 역량으로 취급합니다.
Analyzer는 Coding Agent 프로세스에 **import되지 않습니다**. 생산용 분석·자연어 워크북
변환은 저장소 소유 도구와 전용 subprocess 클라이언트를 통합니다. 채팅 Excel/CSV 업로드,
입력 미리보기, 원본 다운로드, Files 탐색기 삭제는 Coding Agent UI 기능입니다.
구조 검사는 먼저 `inspect_spreadsheet` / `read_spreadsheet`를 사용합니다.

## Runtime path

```
Research Memory Streamlit (코딩 에이전트 탭)
  → chat upload (uploads/) and/or existing workspace files
  → DeepAgentsBridge
    → create_cli_agent(
         tools=inspect_spreadsheet + read_spreadsheet
               + analyze_excel + transform_excel,
         enable_shell=True
       )
      → inspect_spreadsheet / read_spreadsheet  (structure, bounded rows)
      → analyze_excel custom tool
      → transform_excel custom tool
        → dedicated subprocess (process group)
          → excel_ai_analyzer venv: python -m core.application.cli
            → JSON stdout / diagnostic stderr
            → Analyze: <workspace>/outputs/excel_agent/<uuid>/ nested by Analyzer
            → Transform: <workspace>/outputs/excel_agent/<uuid>/ as output_directory
```

Default Coding Agent workspace on this platform:

`data/coding_agent/workspace/` (under the Research Memory repo root).

This host is a Linux server. Timeout handling uses `start_new_session=True`
and `os.killpg` (SIGTERM, then SIGKILL, then wait/reap).

## Environment

Set in the process that runs Streamlit (`./run_app.sh` or Docker), not in
`telegram_memory/.env`.

| Variable | Role | Default |
| --- | --- | --- |
| `CODING_AGENT_EXCEL_ROOT` | `excel_ai_analyzer` repository root. Must contain `core/application/cli.py`. | Sibling `../excel_ai_analyzer` if valid, else `/home/eunbi/excel_ai_analyzer` |
| `CODING_AGENT_EXCEL_PYTHON` | Excel-only Python executable. Coding Agent `sys.executable` is never used as a fallback. | `$ROOT/.venv/bin/python` |
| `CODING_AGENT_EXCEL_TIMEOUT` | Subprocess timeout in seconds (1–86400) | `180` |
| `CODING_AGENT_EXCEL_OUTPUT_ROOT` | Output root **inside** the current Coding Agent workspace | `<workspace>/outputs/excel_agent` |
| `CODING_AGENT_EXCEL_MODEL` | Model name forwarded to Excel Analyzer | same as Coding Agent model unless overridden |
| `CODING_AGENT_EXCEL_OLLAMA` | Ollama base URL forwarded to Excel Analyzer | `http://localhost:11434` |

Invalid configuration does not prevent `import coding_agent` or app startup.
The tool returns `configuration_error` when invoked.

Example:

```bash
export CODING_AGENT_EXCEL_ROOT=/home/eunbi/excel_ai_analyzer
export CODING_AGENT_EXCEL_PYTHON=/home/eunbi/excel_ai_analyzer/.venv/bin/python
export CODING_AGENT_EXCEL_TIMEOUT=180
# optional — defaults to data/coding_agent/workspace/outputs/excel_agent
# export CODING_AGENT_EXCEL_OUTPUT_ROOT=/mnt/data/eunbi/research-memory/data/coding_agent/workspace/outputs/excel_agent
./run_app.sh
```

## Tool usage

Input files may come from chat attachments under `uploads/` or from
files already in the workspace.

- `inspect_spreadsheet`: workbook structure, sheet names, columns, bounded metadata (**prefer first**)
- `read_spreadsheet`: bounded inspection of selected sheet/columns/rows (**prefer for row slices**)
- `analyze_excel(files, prompt, analysis_mode="single", profile_name="generic", sheets=None)`:
  natural-language summary, comparison, aggregation, multi-file analysis
- `transform_excel(files, prompt)`: copy-preserving workbook changes
  (`extract_to_sheet`, `unmerge_cells`). v1 accepts exactly one `.xlsx`. The user
  prompt is forwarded unchanged. Product policies
  (`include_descendants_and_subtotals`, `copy_with_new_sheet`, `fill_all`) are set
  by the adapter and cannot be overridden by the model.

`analyze_excel` notes:

- Pass workspace-relative or workspace-absolute paths (`.xlsx` / `.xls` / `.xlsm` / `.csv`).
- `single` requires one file; `multi` requires two or more.
- Output directory is not a tool argument. Each call writes under
  `outputs/excel_agent/<request-id>/` (visible in Files; Download from the editor).
- Original uploaded files stay in `uploads/`.

`transform_excel` notes:

- Coding Agent creates `outputs/excel_agent/<request-id>/` and passes that directory
  as CLI `output_directory`. Pre-existing request dirs are refused (no silent overwrite).
- The source workbook is not modified; SHA-256 is checked after the run.
- Artifacts are hash-/path-validated inside the workspace before being returned.

## Boundaries

- Built-in filesystem and `execute` tools remain available for non-Excel work.
  Do not use `execute` to run Excel Analyzer CLI.
- Do not reimplement production semantic analysis or coordinate planning with
  pandas in Coding Agent.
- Excel semantic routing and transform planning stay in `excel_ai_analyzer`.
- Coding Agent validates paths, runs the process, enforces timeout, checks the
  JSON contract, and verifies artifact hashes/sizes/workspace containment.
- Existing Coding Agent HITL (shell/write, Auto-approve) is unchanged.
  When Auto-approve is off, `analyze_excel` / `transform_excel` are added to the
  stock HITL interrupt map so the Streamlit Approve/Reject panel gates them
  (deepagents-code has no public `interrupt_on` kwarg; Coding Agent patches the
  interrupt map at agent creation).

## Tests

```bash
cd /mnt/data/eunbi/research-memory
.venv312/bin/python -m pytest coding_agent/tests -q
```
