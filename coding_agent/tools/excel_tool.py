"""Repository-owned Excel custom tool for deepagents-code.

The model-facing function forwards the user's prompt unchanged and does not
choose Excel profiles, rewrite meaning, or call production routers. Those
remain owned by excel_ai_analyzer. This module validates workspace paths,
runs the dedicated subprocess client, and verifies artifact manifests.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from coding_agent.integrations.excel_artifacts import sha256_file, validate_artifacts
from coding_agent.integrations.excel_config import TRANSFORM_POLICIES, load_excel_config
from coding_agent.integrations.excel_errors import (
    CELL_CHAR_LIMIT,
    PREVIEW_COLUMN_LIMIT,
    PREVIEW_ROW_LIMIT,
    TEXT_LIMIT,
    TRANSPORT_ARTIFACT_VALIDATION_FAILED,
    TRANSPORT_CONFIGURATION_ERROR,
    TRANSPORT_EXCEL_RESPONSE,
    TRANSPORT_SOURCE_MUTATED,
)
from coding_agent.integrations.excel_paths import (
    PathValidationError,
    new_request_id,
    prepare_request_output_dir,
    prepare_transform_output_dir,
    validate_tool_inputs,
    validate_transform_inputs,
)
from coding_agent.integrations.excel_subprocess import (
    build_analyze_request,
    build_transform_request,
    run_excel_cli,
)

ANALYZE_EXCEL_NAME = "analyze_excel"

ANALYZE_EXCEL_DESCRIPTION = """\
Analyze Excel or CSV files that already exist in the Coding Agent workspace.

Prefer inspect_spreadsheet / read_spreadsheet for structure or a bounded row \
slice. Use this tool when the user wants natural-language summary, comparison, \
aggregation, quality checks, or a result workbook/chart from Excel Analyzer. \
It is not a general code-generation or code-editing tool.

Files may already be in the workspace, including chat attachments under \
uploads/. Pass workspace-relative paths or absolute paths inside that \
workspace. This tool does not upload files.

analysis_mode:
- single: exactly one file
- multi: two or more files

prompt is the user's natural-language request and is forwarded unchanged to \
Excel Analyzer. Do not rewrite it here.

profile_name is an explicit Excel Analyzer profile. Default is generic. \
Do not auto-select a profile from the prompt.

Results are written under outputs/excel_agent/ (visible in Files) as a copy; \
originals are never modified. Coding Agent does not reinterpret Excel Analyzer \
output.
"""

TRANSFORM_EXCEL_NAME = "transform_excel"

TRANSFORM_EXCEL_DESCRIPTION = """\
Transform one Excel workbook that already exists in the Coding Agent workspace.

Use this tool for copy-preserving structural changes such as extract_to_sheet \
(rows/columns onto a new sheet) or unmerge_cells (unmerge and fill each cell). \
The original file is never overwritten. Results are saved under \
outputs/excel_agent/ with a new request directory (never silently overwritten) \
and appear in Files for preview/download.

For summary, comparison, aggregation, or analysis results, use analyze_excel. \
For structure or a bounded row slice, use inspect_spreadsheet or read_spreadsheet.

v1 accepts exactly one .xlsx file. prompt is the user's natural-language \
request and is forwarded unchanged. Do not invent sheet/row/column coordinates. \
Do not run Excel Analyzer via the execute shell.
"""


def create_excel_tools(*, workspace: Path) -> list[BaseTool]:
    """Factory used by DeepAgentsBridge. Re-run on agent reset."""
    return [
        build_analyze_excel_tool(workspace),
        build_transform_excel_tool(workspace),
    ]


def build_analyze_excel_tool(workspace: Path) -> StructuredTool:
    workspace_path = Path(workspace)

    def analyze_excel(
        files: list[str],
        prompt: str,
        analysis_mode: str = "single",
        profile_name: str = "generic",
        sheets: list[str | int] | None = None,
    ) -> str:
        return run_analyze_excel(
            workspace=workspace_path,
            files=files,
            prompt=prompt,
            analysis_mode=analysis_mode,
            profile_name=profile_name,
            sheets=sheets,
        )

    analyze_excel.__doc__ = ANALYZE_EXCEL_DESCRIPTION
    return StructuredTool.from_function(
        func=analyze_excel,
        name=ANALYZE_EXCEL_NAME,
        description=ANALYZE_EXCEL_DESCRIPTION,
    )


def build_transform_excel_tool(workspace: Path) -> StructuredTool:
    workspace_path = Path(workspace)

    def transform_excel(
        files: list[str],
        prompt: str,
    ) -> str:
        return run_transform_excel(
            workspace=workspace_path,
            files=files,
            prompt=prompt,
        )

    transform_excel.__doc__ = TRANSFORM_EXCEL_DESCRIPTION
    return StructuredTool.from_function(
        func=transform_excel,
        name=TRANSFORM_EXCEL_NAME,
        description=TRANSFORM_EXCEL_DESCRIPTION,
    )


def run_analyze_excel(
    *,
    workspace: Path,
    files: list[str],
    prompt: str,
    analysis_mode: str = "single",
    profile_name: str = "generic",
    sheets: list[str | int] | None = None,
) -> str:
    loaded = load_excel_config(workspace)
    if not loaded.ok or loaded.config is None:
        return format_tool_result(
            {
                "ok": False,
                "error_code": loaded.error_code or TRANSPORT_CONFIGURATION_ERROR,
                "request_id": None,
                "status": None,
                "message": loaded.message,
            }
        )

    config = loaded.config
    try:
        inputs, mode, profile = validate_tool_inputs(
            workspace=workspace,
            files=files,
            prompt=prompt,
            analysis_mode=analysis_mode,
            profile_name=profile_name,
            sheets=sheets,
        )
        request_id = _allocate_request_id(config.output_root, workspace)
        prepare_request_output_dir(config.output_root, workspace, request_id)
    except PathValidationError as exc:
        return format_tool_result(
            {
                "ok": False,
                "error_code": exc.error_code,
                "request_id": None,
                "status": None,
                "message": exc.message,
            }
        )

    request = build_analyze_request(
        config=config,
        request_id=request_id,
        inputs=inputs,
        prompt=prompt,
        analysis_mode=mode,
        profile_name=profile,
        output_directory=config.output_root,
    )
    client = run_excel_cli(config, request)
    return format_tool_result(_client_to_tool_dict(workspace, client, prompt=prompt))


def run_transform_excel(
    *,
    workspace: Path,
    files: list[str],
    prompt: str,
) -> str:
    loaded = load_excel_config(workspace)
    if not loaded.ok or loaded.config is None:
        return format_tool_result(
            {
                "ok": False,
                "error_code": loaded.error_code or TRANSPORT_CONFIGURATION_ERROR,
                "request_id": None,
                "status": None,
                "message": loaded.message,
            }
        )

    config = loaded.config
    try:
        source = validate_transform_inputs(workspace=workspace, files=files, prompt=prompt)
        request_id = _allocate_request_id(config.output_root, workspace)
        output_directory = prepare_transform_output_dir(
            config.output_root, workspace, request_id
        )
    except PathValidationError as exc:
        return format_tool_result(
            {
                "ok": False,
                "error_code": exc.error_code,
                "request_id": None,
                "status": None,
                "message": exc.message,
            }
        )

    source_sha = sha256_file(source.path)
    request = build_transform_request(
        config=config,
        request_id=request_id,
        source=source,
        prompt=prompt,
        output_directory=output_directory,
    )
    # Policies are product defaults on this adapter. The model cannot override them.
    request["policies"] = dict(TRANSFORM_POLICIES)
    client = run_excel_cli(config, request)
    try:
        after_sha = sha256_file(source.path)
    except OSError:
        after_sha = ""
    if after_sha != source_sha:
        return format_tool_result(
            {
                "ok": False,
                "error_code": TRANSPORT_SOURCE_MUTATED,
                "request_id": request_id,
                "status": None,
                "message": "Source workbook changed during transformation.",
                "artifacts": [],
            }
        )
    return format_tool_result(
        _client_to_tool_dict(
            workspace,
            client,
            prompt=prompt,
            containment_root=output_directory,
            source_paths=(source.path,),
            workbook_only_on_success=True,
        )
    )


def format_tool_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _allocate_request_id(output_root: Path, workspace: Path) -> str:
    for _ in range(8):
        request_id = new_request_id()
        dest = output_root.resolve() / request_id
        if not dest.exists():
            return request_id
    raise PathValidationError(
        TRANSPORT_CONFIGURATION_ERROR,
        "Could not allocate a unique Excel request directory.",
    )


def _client_to_tool_dict(
    workspace: Path,
    client,
    *,
    prompt: str,
    containment_root: Path | None = None,
    source_paths: tuple[Path, ...] | None = None,
    workbook_only_on_success: bool = False,
) -> dict[str, Any]:
    # prompt is accepted only so tests can prove it is not rewritten. It is not
    # substituted into the Excel response text.
    del prompt
    if client.transport_error or client.payload is None:
        result: dict[str, Any] = {
            "ok": False,
            "error_code": client.transport_error,
            "request_id": client.request_id or None,
            "status": None,
            "message": client.message,
        }
        if client.diagnostic:
            result["diagnostic"] = client.diagnostic
        if client.exit_code is not None:
            result["exit_code"] = client.exit_code
        return result

    payload = client.payload
    excel_status = str(payload.get("status") or client.excel_status or "")
    verified, rejected = validate_artifacts(
        workspace=workspace,
        artifacts=payload.get("artifacts"),
        excel_status=excel_status,
        containment_root=containment_root,
        source_paths=source_paths,
        workbook_only_on_success=workbook_only_on_success,
    )
    error_code = None
    ok = excel_status == "success" and not rejected
    if rejected and excel_status == "success":
        error_code = TRANSPORT_ARTIFACT_VALIDATION_FAILED
    elif excel_status != "success":
        error_code = TRANSPORT_EXCEL_RESPONSE

    text = _bound_text(str(payload.get("text") or ""), TEXT_LIMIT)
    result = {
        "ok": ok,
        "error_code": error_code,
        "request_id": payload.get("request_id") or client.request_id,
        "status": excel_status,
        "text": text,
        "preview": _bound_preview(payload.get("data")),
        "warnings": _bound_warnings(payload.get("warnings")),
        "safety": payload.get("safety") if isinstance(payload.get("safety"), dict) else {},
        "artifacts": verified,
        "output_directory": _request_output_directory(
            verified, str(payload.get("request_id") or client.request_id), workspace
        ),
    }
    if rejected:
        result["rejected_artifacts"] = rejected
    if isinstance(payload.get("error"), dict):
        result["excel_error"] = {
            "code": payload["error"].get("code"),
            "message": _bound_text(str(payload["error"].get("message") or ""), 500),
            "stage": payload["error"].get("stage"),
            "retryable": payload["error"].get("retryable"),
        }
    return result


def _request_output_directory(
    artifacts: list[dict[str, Any]],
    request_id: str,
    workspace: Path,
) -> str | None:
    if artifacts:
        parent = Path(artifacts[0]["path"]).parent
        try:
            return str(parent.resolve().relative_to(workspace.resolve()))
        except ValueError:
            return str(parent)
    if request_id:
        return f"outputs/excel_agent/{request_id}"
    return None


def _bound_preview(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    columns = data.get("columns")
    if not isinstance(columns, list):
        columns = []
    records = data.get("preview_records")
    if not isinstance(records, list):
        records = []
    bounded_records = []
    for row in records[:PREVIEW_ROW_LIMIT]:
        if not isinstance(row, dict):
            continue
        bounded_records.append(
            {
                str(key)[:CELL_CHAR_LIMIT]: _bound_cell(value)
                for key, value in list(row.items())[:PREVIEW_COLUMN_LIMIT]
            }
        )
    preview: dict[str, Any] = {
        "shape": data.get("shape"),
        "columns": [str(col) for col in columns[:PREVIEW_COLUMN_LIMIT]],
        "preview_records": bounded_records,
    }
    if data.get("route"):
        preview["route"] = str(data.get("route"))
    if data.get("analysis_mode"):
        preview["analysis_mode"] = str(data.get("analysis_mode"))
    if data.get("operation_name"):
        preview["operation_name"] = str(data.get("operation_name"))
    return preview


def _bound_warnings(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    bounded: list[dict[str, str]] = []
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        bounded.append(
            {
                "code": str(item.get("code") or ""),
                "message": _bound_text(str(item.get("message") or ""), 300),
                "stage": str(item.get("stage") or ""),
            }
        )
    return bounded


def _bound_cell(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return _bound_text(str(value), CELL_CHAR_LIMIT)


def _bound_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
