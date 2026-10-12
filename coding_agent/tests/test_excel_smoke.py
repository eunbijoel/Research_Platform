"""Live Phase I1 CLI smoke. Skips when the Excel environment is not configured."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.config import EXCEL_CLI_MODULE_RELATIVE
from coding_agent.integrations.excel_config import load_excel_config
from coding_agent.tools.excel_tool import run_analyze_excel


def _excel_ready(workspace: Path) -> tuple[bool, str]:
    loaded = load_excel_config(workspace)
    if not loaded.ok or loaded.config is None:
        return False, loaded.message
    config = loaded.config
    if not (config.excel_root / EXCEL_CLI_MODULE_RELATIVE).is_file():
        return False, "Excel CLI module missing"
    if not config.python_executable.is_file():
        return False, "Excel Python missing"
    return True, ""


def test_phase_i1_cli_deterministic_smoke(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("CODING_AGENT_EXCEL_OUTPUT_ROOT", str(workspace / "outputs/excel_agent"))
    ready, reason = _excel_ready(workspace)
    if not ready:
        pytest.skip(f"Excel Analyzer environment is not available: {reason}")

    from coding_agent.integrations.excel_config import load_excel_config as load

    config = load(workspace).config
    assert config is not None
    fixture = workspace / "sample.xlsx"
    created = _write_xlsx_with_excel_python(config.python_executable, fixture)
    if not created:
        pytest.skip("Excel Python could not create an xlsx fixture")

    raw = run_analyze_excel(
        workspace=workspace,
        files=["sample.xlsx"],
        prompt="이 파일의 주요 내용을 요약해줘",
        analysis_mode="single",
        profile_name="generic",
    )
    payload = json.loads(raw)
    assert payload["status"] == "success"
    assert payload["error_code"] is None
    assert payload["ok"] is True
    assert payload["request_id"]
    text = payload.get("text") or ""
    preview = payload.get("preview") or {}
    assert text or preview.get("preview_records") or preview.get("columns")
    assert "컬럼" in text or "이 파일" in text
    request_dir = (workspace / "outputs/excel_agent" / str(payload["request_id"])).resolve()
    assert request_dir.is_dir()
    assert request_dir.is_relative_to(workspace.resolve())
    for artifact in payload.get("artifacts") or []:
        path = Path(artifact["path"])
        assert path.is_file()
        assert path.resolve().is_relative_to(workspace.resolve())
        rel = artifact["workspace_relative_path"]
        assert not rel.startswith("/")
        assert (workspace / rel).resolve() == path.resolve()
    output_dir = payload.get("output_directory")
    if output_dir:
        resolved = (workspace / output_dir).resolve()
        assert resolved.is_relative_to(workspace.resolve())

    schema_raw = run_analyze_excel(
        workspace=workspace,
        files=["sample.xlsx"],
        prompt="컬럼 목록 보여줘",
        analysis_mode="single",
        profile_name="generic",
    )
    schema = json.loads(schema_raw)
    assert schema["status"] == "success"
    artifacts = schema.get("artifacts") or []
    assert artifacts, "schema early-route should materialize a table artifact"
    for artifact in artifacts:
        path = Path(artifact["path"])
        assert path.is_file()
        assert path.resolve().is_relative_to(workspace.resolve())
        assert artifact["sha256"]
        assert artifact["size_bytes"] == path.stat().st_size



def _write_xlsx_with_excel_python(python: Path, dest: Path) -> bool:
    import subprocess

    script = (
        "import sys; from pathlib import Path;"
        "import pandas as pd;"
        f"p = Path({str(dest)!r});"
        "pd.DataFrame({'항목': ['A', 'B'], '값': [1, 2]}).to_excel(p, index=False);"
        "print(p.exists())"
    )
    try:
        result = subprocess.run(
            [str(python), "-c", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and dest.is_file()
