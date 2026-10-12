from __future__ import annotations

import json
from pathlib import Path

from langchain_core.tools import BaseTool, StructuredTool

from coding_agent.integrations.excel_subprocess import ExcelClientResult
from coding_agent.tools.excel_tool import (
    ANALYZE_EXCEL_DESCRIPTION,
    ANALYZE_EXCEL_NAME,
    build_analyze_excel_tool,
    format_tool_result,
    run_analyze_excel,
)
from coding_agent.tests.conftest import bind_excel_env, install_fake_excel_root, write_bytes


def _tool_schema(tool: StructuredTool) -> dict:
    schema = tool.args_schema
    if hasattr(schema, "model_json_schema"):
        return schema.model_json_schema()
    return schema.schema()


def test_tool_name_description_schema(workspace: Path) -> None:
    tool = build_analyze_excel_tool(workspace)
    assert tool.name == ANALYZE_EXCEL_NAME
    assert "Excel or CSV" in tool.description or "Excel/CSV" in tool.description or "Excel" in tool.description
    assert "code-generation" in tool.description or "code-editing" in tool.description
    schema = _tool_schema(tool)
    props = schema.get("properties") or schema.get("required")
    assert "files" in schema.get("properties", {})
    assert "prompt" in schema.get("properties", {})
    assert "output_directory" not in schema.get("properties", {})
    assert "analysis_mode" in schema.get("properties", {})
    assert isinstance(tool, BaseTool)


def test_deepagents_callable_compatibility(workspace: Path) -> None:
    tools = [build_analyze_excel_tool(workspace)]
    names = [
        getattr(item, "name", None) or getattr(item, "__name__", None) for item in tools
    ]
    assert names == [ANALYZE_EXCEL_NAME]


def test_prompt_passed_unchanged(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    dump = tmp_path / "request.json"
    bind_excel_env(
        monkeypatch,
        root=root,
        python=Path(__import__("sys").executable),
        timeout="5",
        output_root=workspace / "outputs/excel_agent",
    )
    monkeypatch.setenv("FAKE_EXCEL_DUMP", str(dump))
    write_bytes(workspace / "data.xlsx")
    prompt = "이 파일의 주요 내용을 요약해줘"
    raw = run_analyze_excel(
        workspace=workspace,
        files=["data.xlsx"],
        prompt=prompt,
        profile_name="generic",
    )
    payload = json.loads(raw)
    dumped = json.loads(dump.read_text(encoding="utf-8"))
    assert dumped["user_prompt"] == prompt
    assert dumped["profile_name"] == "generic"
    assert dumped["user_prompt"] != "AUTO_SUMMARY"
    assert "output_directory" not in _tool_schema(build_analyze_excel_tool(workspace)).get("properties", {})
    assert payload["status"] == "success"


def test_profile_not_auto_selected(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    dump = tmp_path / "request.json"
    bind_excel_env(
        monkeypatch,
        root=root,
        python=Path(__import__("sys").executable),
        timeout="5",
        output_root=workspace / "outputs/excel_agent",
    )
    monkeypatch.setenv("FAKE_EXCEL_DUMP", str(dump))
    write_bytes(workspace / "budget.xlsx")
    run_analyze_excel(
        workspace=workspace,
        files=["budget.xlsx"],
        prompt="연구활동비 예산을 분석해줘",
    )
    dumped = json.loads(dump.read_text(encoding="utf-8"))
    assert dumped["profile_name"] == "generic"


def test_bounded_result_and_status_preserved() -> None:
    payload = {
        "ok": False,
        "error_code": "excel_response",
        "request_id": "abc",
        "status": "validation_failed",
        "text": "x" * 50,
        "preview": {"preview_records": []},
        "warnings": [],
        "safety": {"validation_status": "failed"},
        "artifacts": [],
    }
    raw = format_tool_result(payload)
    parsed = json.loads(raw)
    assert parsed["status"] == "validation_failed"
    assert parsed["error_code"] == "excel_response"
    assert parsed["ok"] is False


def test_transport_error_formatting(monkeypatch, workspace: Path) -> None:
    monkeypatch.setenv("CODING_AGENT_EXCEL_ROOT", str(workspace / "missing-excel"))
    raw = run_analyze_excel(
        workspace=workspace,
        files=["data.xlsx"],
        prompt="요약해줘",
    )
    parsed = json.loads(raw)
    assert parsed["error_code"] == "configuration_error"
    assert parsed["status"] is None
    assert "Traceback" not in raw


def test_output_directory_not_a_tool_argument(workspace: Path) -> None:
    schema = _tool_schema(build_analyze_excel_tool(workspace))
    assert "output_directory" not in schema.get("properties", {})
    required = schema.get("required") or []
    assert "output_directory" not in required
    assert "files" in required
    assert "prompt" in required
