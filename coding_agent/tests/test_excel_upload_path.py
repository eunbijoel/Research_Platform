from __future__ import annotations

import json
import sys
from pathlib import Path

from coding_agent.integrations.excel_paths import validate_tool_inputs
from coding_agent.spreadsheet import save_upload
from coding_agent.tools.excel_tool import run_analyze_excel
from coding_agent.tests.conftest import bind_excel_env, install_fake_excel_root


def test_upload_path_is_valid_analyze_excel_input(
    monkeypatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    csv_bytes = b"item,value\nA,1\nB,2\n"
    result = save_upload(workspace, filename="sample.csv", data=csv_bytes)
    rel = result["path"]
    assert rel.startswith("uploads/")
    uploaded = workspace / rel
    assert uploaded.is_file()
    assert uploaded.read_bytes() == csv_bytes

    validated, mode, profile = validate_tool_inputs(
        workspace=workspace,
        files=[rel],
        prompt="schema only",
        analysis_mode="single",
        profile_name="generic",
        sheets=None,
    )
    assert mode == "single"
    assert profile == "generic"
    assert len(validated) == 1
    assert validated[0].path == uploaded.resolve()
    assert validated[0].path.is_relative_to(workspace.resolve())
    assert validated[0].workspace_relative.replace("\\", "/") == rel.replace("\\", "/")
    assert uploaded.read_bytes() == csv_bytes

    root = install_fake_excel_root(tmp_path / "excel")
    bind_excel_env(
        monkeypatch,
        root=root,
        python=Path(sys.executable),
        timeout="5",
        output_root=workspace / "outputs/excel_agent",
    )
    raw = run_analyze_excel(
        workspace=workspace,
        files=[rel],
        prompt="schema only",
        analysis_mode="single",
        profile_name="generic",
    )
    payload = json.loads(raw)
    assert payload["status"] == "success"
    assert payload["ok"] is True
    request_id = str(payload["request_id"])
    assert request_id
    output_dir = payload.get("output_directory")
    assert output_dir == f"outputs/excel_agent/{request_id}"
    excel_root = (workspace / "outputs/excel_agent").resolve()
    assert excel_root.is_dir()
    assert excel_root.is_relative_to(workspace.resolve())
    assert uploaded.read_bytes() == csv_bytes
    assert not uploaded.resolve().is_relative_to(excel_root)
