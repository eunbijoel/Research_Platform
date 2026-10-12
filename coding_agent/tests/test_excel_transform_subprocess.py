"""Transform request builder and shared CLI subprocess tests (Phase I2-D3D)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from coding_agent.integrations.excel_config import TRANSFORM_POLICIES
from coding_agent.integrations.excel_paths import ValidatedInput
from coding_agent.integrations.excel_subprocess import (
    build_analyze_request,
    build_transform_request,
    run_excel_cli,
)
from coding_agent.tests.conftest import fake_config, install_fake_excel_root


def _source(workspace: Path) -> ValidatedInput:
    path = workspace / "data.xlsx"
    path.write_bytes(b"xlsx")
    return ValidatedInput(
        source_id="file-1",
        path=path.resolve(),
        display_name="data.xlsx",
        sheet=0,
        workspace_relative="data.xlsx",
    )


def test_build_transform_request_contract(workspace: Path) -> None:
    config = fake_config(excel_root=workspace, workspace=workspace)
    output = workspace / "outputs/excel_agent" / "abc123"
    output.mkdir(parents=True)
    prompt = "한글 원문 그대로"
    request = build_transform_request(
        config=config,
        request_id="abc123",
        source=_source(workspace),
        prompt=prompt,
        output_directory=output,
    )
    assert request["operation"] == "transform"
    assert request["user_prompt"] == prompt
    assert request["policies"] == TRANSFORM_POLICIES
    assert request["output_directory"] == str(output.resolve())
    assert request["source"]["path"] == str((workspace / "data.xlsx").resolve())
    assert "transformations" not in request
    assert "inputs" not in request
    assert request["timeout_seconds"] == config.timeout_seconds
    assert request["model"]["name"] == config.model_name


def test_analyze_builder_unchanged(workspace: Path) -> None:
    config = fake_config(excel_root=workspace, workspace=workspace)
    request = build_analyze_request(
        config=config,
        request_id="r1",
        inputs=[_source(workspace)],
        prompt="요약해줘",
        analysis_mode="single",
        profile_name="generic",
        output_directory=config.output_root,
    )
    assert request["operation"] == "analyze"
    assert "policies" not in request
    assert request["output_directory"] == str(config.output_root.resolve())


def test_transform_cli_dumps_and_status(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    dump = tmp_path / "req.json"
    monkeypatch.setenv("FAKE_EXCEL_DUMP", str(dump))
    config = fake_config(excel_root=root, workspace=workspace, python=Path(sys.executable))
    output = workspace / "outputs/excel_agent" / "rid"
    output.mkdir(parents=True)
    request = build_transform_request(
        config=config,
        request_id="rid",
        source=_source(workspace),
        prompt="Extract columns A and C",
        output_directory=output,
    )
    result = run_excel_cli(config, request)
    assert result.transport_error is None
    assert result.excel_status == "success"
    dumped = json.loads(dump.read_text(encoding="utf-8"))
    assert dumped["operation"] == "transform"
    assert dumped["user_prompt"] == "Extract columns A and C"
    assert dumped["policies"] == TRANSFORM_POLICIES
    command = [str(config.python_executable), "-m", "core.application.cli"]
    assert command[1:] == ["-m", "core.application.cli"]


def test_excel_application_statuses_preserved(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    config = fake_config(excel_root=root, workspace=workspace, python=Path(sys.executable))
    output = workspace / "outputs/excel_agent" / "rid"
    output.mkdir(parents=True)
    source = _source(workspace)
    for status in (
        "cannot_plan",
        "model_unavailable",
        "timeout",
        "invalid_request",
        "execution_failed",
        "validation_failed",
    ):
        monkeypatch.setenv("FAKE_EXCEL_MODE", status if status != "validation_failed" else "excel_error")
        request = build_transform_request(
            config=config,
            request_id="rid",
            source=source,
            prompt="prompt",
            output_directory=output,
        )
        result = run_excel_cli(config, request)
        assert result.transport_error is None
        expected = "validation_failed" if status == "validation_failed" else status
        assert result.excel_status == expected


def test_transform_malformed_and_timeout_are_transport(
    monkeypatch, tmp_path: Path, workspace: Path
) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    config = fake_config(
        excel_root=root, workspace=workspace, python=Path(sys.executable), timeout_seconds=0.8
    )
    output = workspace / "outputs/excel_agent" / "rid"
    output.mkdir(parents=True)
    source = _source(workspace)
    request = build_transform_request(
        config=config,
        request_id="rid",
        source=source,
        prompt="prompt",
        output_directory=output,
    )
    monkeypatch.setenv("FAKE_EXCEL_MODE", "malformed")
    bad = run_excel_cli(config, request)
    assert bad.transport_error == "malformed_response"
    assert bad.excel_status is None
    monkeypatch.setenv("FAKE_EXCEL_MODE", "empty")
    empty = run_excel_cli(config, request)
    assert empty.transport_error == "malformed_response"
    monkeypatch.setenv("FAKE_EXCEL_MODE", "sleep")
    timed = run_excel_cli(config, request, timeout_seconds=0.3)
    assert timed.transport_error == "subprocess_timeout"
    assert timed.excel_status is None


def test_unicode_transform_prompt(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    dump = tmp_path / "req.json"
    monkeypatch.setenv("FAKE_EXCEL_DUMP", str(dump))
    monkeypatch.setenv("FAKE_EXCEL_MODE", "unicode")
    config = fake_config(excel_root=root, workspace=workspace, python=Path(sys.executable))
    output = workspace / "outputs/excel_agent" / "rid"
    output.mkdir(parents=True)
    request = build_transform_request(
        config=config,
        request_id="rid",
        source=_source(workspace),
        prompt="한글 요청 원문",
        output_directory=output,
    )
    result = run_excel_cli(config, request)
    assert result.payload is not None
    assert result.payload["text"] == "한글 요약 완료"
    dumped = json.loads(dump.read_text(encoding="utf-8"))
    assert dumped["user_prompt"] == "한글 요청 원문"
