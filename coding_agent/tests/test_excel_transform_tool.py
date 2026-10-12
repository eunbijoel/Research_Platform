"""Natural-language transform_excel tool tests (Phase I2-D3D)."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.tools import StructuredTool

from coding_agent.integrations.excel_artifacts import sha256_file
from coding_agent.integrations.excel_config import TRANSFORM_POLICIES
from coding_agent.tools.excel_tool import (
    TRANSFORM_EXCEL_DESCRIPTION,
    TRANSFORM_EXCEL_NAME,
    build_analyze_excel_tool,
    build_transform_excel_tool,
    create_excel_tools,
    run_analyze_excel,
    run_transform_excel,
)
from coding_agent.tests.conftest import bind_excel_env, install_fake_excel_root, write_bytes


def _tool_schema(tool: StructuredTool) -> dict:
    schema = tool.args_schema
    if hasattr(schema, "model_json_schema"):
        return schema.model_json_schema()
    return schema.schema()


def _bind(monkeypatch, tmp_path: Path, workspace: Path, *, write_artifact: bool = False) -> Path:
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
    if write_artifact:
        monkeypatch.setenv("FAKE_EXCEL_WRITE_TRANSFORM", "1")
    return dump


def test_tool_name_and_arguments_only_files_prompt(workspace: Path) -> None:
    tool = build_transform_excel_tool(workspace)
    assert tool.name == TRANSFORM_EXCEL_NAME
    schema = _tool_schema(tool)
    props = schema.get("properties", {})
    assert set(props) == {"files", "prompt"}
    assert "policies" not in props
    assert "output_directory" not in props
    assert "model" not in props
    assert "sheet" not in props
    assert "rows" not in props
    assert "columns" not in props
    assert "transformations" not in props
    assert "unmerge_fill_policy" not in props
    assert "execute" not in TRANSFORM_EXCEL_DESCRIPTION.lower() or "do not run" in TRANSFORM_EXCEL_DESCRIPTION.lower()
    names = [item.name for item in create_excel_tools(workspace=workspace)]
    assert names == ["analyze_excel", TRANSFORM_EXCEL_NAME]


def test_xlsx_exactly_one_success(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    dump = _bind(monkeypatch, tmp_path, workspace, write_artifact=True)
    source = write_bytes(workspace / "data.xlsx", b"source-bytes")
    before = sha256_file(source)
    prompt = "Extract visible rows onto a new sheet named Extracted."
    raw = run_transform_excel(workspace=workspace, files=["data.xlsx"], prompt=prompt)
    payload = json.loads(raw)
    dumped = json.loads(dump.read_text(encoding="utf-8"))
    assert payload["status"] == "success"
    assert payload["ok"] is True
    assert dumped["operation"] == "transform"
    assert dumped["user_prompt"] == prompt
    assert dumped["policies"] == TRANSFORM_POLICIES
    assert dumped["source"]["path"] == str(source.resolve())
    assert dumped["timeout_seconds"] == 5.0 or dumped["timeout_seconds"] == 5
    rel = Path(dumped["output_directory"]).resolve().relative_to(workspace.resolve())
    assert rel.parts[:2] == ("outputs", "excel_agent")
    assert len(rel.parts) == 3
    assert dumped["request_id"] == rel.parts[2]
    nested = Path(dumped["output_directory"]) / dumped["request_id"]
    assert not nested.exists()
    assert sha256_file(source) == before
    assert payload["artifacts"]
    artifact = payload["artifacts"][0]
    assert artifact["kind"] == "workbook"
    assert Path(artifact["path"]).is_file()
    assert Path(artifact["path"]).resolve().is_relative_to(Path(dumped["output_directory"]).resolve())
    assert Path(artifact["path"]).resolve() != source.resolve()


def test_zero_and_multiple_files_rejected(
    monkeypatch, tmp_path: Path, workspace: Path
) -> None:
    import sys

    from coding_agent.tests.conftest import bind_excel_env, clear_excel_env, install_fake_excel_root

    clear_excel_env(monkeypatch)
    excel_root = install_fake_excel_root(tmp_path / "excel")
    bind_excel_env(
        monkeypatch,
        root=excel_root,
        python=Path(sys.executable),
        timeout="5",
        output_root=workspace / "outputs" / "excel_agent",
    )
    write_bytes(workspace / "a.xlsx")
    write_bytes(workspace / "b.xlsx")
    empty = json.loads(run_transform_excel(workspace=workspace, files=[], prompt="extract rows"))
    assert empty["error_code"] == "invalid_tool_input"
    many = json.loads(
        run_transform_excel(workspace=workspace, files=["a.xlsx", "b.xlsx"], prompt="extract rows")
    )
    assert many["error_code"] == "invalid_tool_input"
    assert many["status"] is None


def test_legacy_extensions_rejected_analyze_unchanged(
    monkeypatch, tmp_path: Path, workspace: Path
) -> None:
    dump = _bind(monkeypatch, tmp_path, workspace)
    write_bytes(workspace / "old.xls")
    write_bytes(workspace / "macro.xlsm")
    write_bytes(workspace / "table.csv", b"a,b\n1,2\n")
    write_bytes(workspace / "ok.xlsx")
    for name in ("old.xls", "macro.xlsm", "table.csv"):
        parsed = json.loads(run_transform_excel(workspace=workspace, files=[name], prompt="extract rows"))
        assert parsed["error_code"] == "invalid_tool_input"
        assert "Unsupported file extension" in parsed["message"]
    analyzed = json.loads(
        run_analyze_excel(workspace=workspace, files=["table.csv"], prompt="이 파일 요약해줘")
    )
    assert analyzed["status"] == "success"
    dumped = json.loads(dump.read_text(encoding="utf-8"))
    assert dumped["operation"] == "analyze"


def test_blank_prompt_rejected_and_verbatim_prompt(
    monkeypatch, tmp_path: Path, workspace: Path
) -> None:
    dump = _bind(monkeypatch, tmp_path, workspace)
    write_bytes(workspace / "data.xlsx")
    blank = json.loads(run_transform_excel(workspace=workspace, files=["data.xlsx"], prompt="   "))
    assert blank["error_code"] == "invalid_tool_input"
    prompt = "  Keep spacing and 한글 tokens."
    run_transform_excel(workspace=workspace, files=["data.xlsx"], prompt=prompt)
    dumped = json.loads(dump.read_text(encoding="utf-8"))
    assert dumped["user_prompt"] == prompt
    assert "transformations" not in dumped


def test_policies_forced_and_not_overridable(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    dump = _bind(monkeypatch, tmp_path, workspace)
    write_bytes(workspace / "data.xlsx")
    run_transform_excel(workspace=workspace, files=["data.xlsx"], prompt="unmerge everything")
    dumped = json.loads(dump.read_text(encoding="utf-8"))
    assert dumped["policies"] == {
        "group_scope": "include_descendants_and_subtotals",
        "output_mode": "copy_with_new_sheet",
        "unmerge_fill_policy": "fill_all",
    }
    schema = _tool_schema(build_transform_excel_tool(workspace))
    assert "policies" not in schema.get("properties", {})


def test_preexisting_output_dir_not_overwritten(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    _bind(monkeypatch, tmp_path, workspace)
    write_bytes(workspace / "data.xlsx")
    request_id = "a" * 32
    dest = workspace / "outputs/excel_agent" / request_id
    dest.mkdir(parents=True)
    sentinel = dest / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    monkeypatch.setattr("coding_agent.tools.excel_tool.new_request_id", lambda: request_id)
    parsed = json.loads(run_transform_excel(workspace=workspace, files=["data.xlsx"], prompt="extract"))
    assert parsed["ok"] is False
    assert parsed["error_code"] in {"invalid_tool_input", "configuration_error"}
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_cannot_plan_blocks_artifacts(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    _bind(monkeypatch, tmp_path, workspace)
    monkeypatch.setenv("FAKE_EXCEL_MODE", "cannot_plan")
    write_bytes(workspace / "data.xlsx")
    parsed = json.loads(run_transform_excel(workspace=workspace, files=["data.xlsx"], prompt="maybe extract"))
    assert parsed["status"] == "cannot_plan"
    assert parsed["ok"] is False
    assert parsed["error_code"] == "excel_response"
    assert parsed["artifacts"] == []


def test_failure_status_workbook_blocked(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    dump = _bind(monkeypatch, tmp_path, workspace, write_artifact=True)
    monkeypatch.setenv("FAKE_EXCEL_MODE", "execution_failed")
    write_bytes(workspace / "data.xlsx")
    parsed = json.loads(run_transform_excel(workspace=workspace, files=["data.xlsx"], prompt="extract"))
    assert parsed["status"] == "execution_failed"
    assert parsed["artifacts"] == []
    dumped = json.loads(dump.read_text(encoding="utf-8"))
    assert dumped["operation"] == "transform"


def test_analyze_tool_schema_unchanged(workspace: Path) -> None:
    schema = _tool_schema(build_analyze_excel_tool(workspace))
    assert "analysis_mode" in schema.get("properties", {})
    assert "profile_name" in schema.get("properties", {})


def test_no_demo_or_coordinate_hardcoding() -> None:
    # tests live under coding_agent/tests/ → package root is parents[1]
    root = Path(__file__).resolve().parents[1]
    needles = ("당해예산", "내부인건비", "재료비", "예실대비표", "04_분산")
    files = [
        root / "tools" / "excel_tool.py",
        root / "integrations" / "excel_subprocess.py",
        root / "integrations" / "excel_paths.py",
        root / "integrations" / "excel_config.py",
        root / "bridge.py",
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in text
        assert "if \"병합\"" not in text
        assert "value_match" not in text
        assert '"op": "extract_to_sheet"' not in text
        assert "당해예산" not in text
    subprocess_text = (root / "integrations" / "excel_subprocess.py").read_text(encoding="utf-8")
    assert "build_transform_request" in subprocess_text
    assert "shell=False" in subprocess_text
