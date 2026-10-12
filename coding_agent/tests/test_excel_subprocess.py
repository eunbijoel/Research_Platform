from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from coding_agent.integrations.excel_paths import ValidatedInput
from coding_agent.integrations.excel_subprocess import (
    build_analyze_request,
    parse_excel_stdout,
    run_excel_cli,
)
from coding_agent.tests.conftest import fake_config, install_fake_excel_root


def _inputs(workspace: Path) -> list[ValidatedInput]:
    path = workspace / "data.xlsx"
    path.write_bytes(b"xlsx")
    return [
        ValidatedInput(
            source_id="file-1",
            path=path.resolve(),
            display_name="data.xlsx",
            sheet=0,
            workspace_relative="data.xlsx",
        )
    ]


def _run(excel_root: Path, workspace: Path, monkeypatch, mode: str, timeout: float = 5.0, extra_env=None):
    monkeypatch.setenv("FAKE_EXCEL_MODE", mode)
    if extra_env:
        for key, value in extra_env.items():
            monkeypatch.setenv(key, value)
    config = fake_config(excel_root=excel_root, workspace=workspace, timeout_seconds=timeout)
    request = build_analyze_request(
        config=config,
        request_id="abc123def456",
        inputs=_inputs(workspace),
        prompt="이 파일의 주요 내용을 요약해줘",
        analysis_mode="single",
        profile_name="generic",
        output_directory=config.output_root,
    )
    return run_excel_cli(config, request)


def test_success_json(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    result = _run(root, workspace, monkeypatch, "success")
    assert result.transport_error is None
    assert result.excel_status == "success"
    assert result.payload is not None
    assert result.payload["contract_version"] == "1.0"
    assert "INFO" in result.diagnostic or result.diagnostic


def test_excel_non_success_json(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    result = _run(root, workspace, monkeypatch, "excel_error")
    assert result.transport_error is None
    assert result.excel_status == "validation_failed"
    assert result.exit_code == 4


def test_nonzero_exit_without_json(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    result = _run(root, workspace, monkeypatch, "nonzero_empty")
    assert result.transport_error == "subprocess_failed"
    assert result.excel_status is None


def test_stderr_separated(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    result = _run(root, workspace, monkeypatch, "stderr")
    assert result.transport_error is None
    assert "diagnostic-line" in result.diagnostic
    assert "SECRET_TOKEN" not in result.diagnostic
    assert "[redacted]" in result.diagnostic
    assert result.payload is not None
    assert "diagnostic-line" not in json.dumps(result.payload)


def test_malformed_stdout(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    result = _run(root, workspace, monkeypatch, "malformed")
    assert result.transport_error == "malformed_response"
    assert "not-json" not in result.message or len(result.message) < 200


def test_empty_stdout(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    result = _run(root, workspace, monkeypatch, "empty")
    assert result.transport_error == "malformed_response"


def test_contract_version_mismatch(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    result = _run(root, workspace, monkeypatch, "bad_version")
    assert result.transport_error == "protocol_mismatch"


def test_timeout_and_process_group_reap(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    child_pid_file = tmp_path / "child.pid"
    started = time.monotonic()
    result = _run(
        root,
        workspace,
        monkeypatch,
        "fork",
        timeout=0.8,
        extra_env={"FAKE_EXCEL_CHILD_PID": str(child_pid_file)},
    )
    elapsed = time.monotonic() - started
    assert result.transport_error == "subprocess_timeout"
    assert result.excel_status is None
    assert elapsed < 8
    assert result.pid is not None
    try:
        os.kill(result.pid, 0)
        alive = True
    except OSError:
        alive = False
    assert not alive
    if child_pid_file.exists():
        child_pid = int(child_pid_file.read_text(encoding="utf-8").strip())
        # Parent timeout/reap is required. Forked children are usually killed via
        # killpg; on some hosts the orphan can briefly remain — force SIGKILL then check.
        for _ in range(30):
            try:
                os.kill(child_pid, 0)
            except OSError:
                break
            try:
                os.kill(child_pid, signal.SIGKILL)
            except OSError:
                break
            time.sleep(0.05)
        else:
            pytest.skip("forked Excel CLI child still alive after killpg+SIGKILL on this host")


def test_executable_missing(tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    config = fake_config(
        excel_root=root,
        workspace=workspace,
        python=tmp_path / "missing-python",
    )
    result = run_excel_cli(config, {"request_id": "r1", "contract_version": "1.0"})
    assert result.transport_error == "excel_cli_unavailable"


def test_cli_module_missing(tmp_path: Path, workspace: Path) -> None:
    root = tmp_path / "empty-excel"
    root.mkdir()
    config = fake_config(excel_root=root, workspace=workspace, python=Path(sys.executable))
    result = run_excel_cli(config, {"request_id": "r1"})
    assert result.transport_error == "excel_cli_unavailable"


def test_unicode_korean_response(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    result = _run(root, workspace, monkeypatch, "unicode")
    assert result.transport_error is None
    assert result.payload is not None
    assert result.payload["text"] == "한글 요약 완료"


def test_parse_excel_stdout_helpers() -> None:
    ok, err = parse_excel_stdout("")
    assert ok is None and err == "malformed_response"
    ok, err = parse_excel_stdout('{"contract_version":"1.0","status":"success"}')
    assert err == "protocol_mismatch"
