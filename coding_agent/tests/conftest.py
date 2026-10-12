"""Shared fixtures for Excel integration tests."""

from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from coding_agent.config import (
    DEFAULT_EXCEL_MODEL,
    EXCEL_MODEL_ENV,
    EXCEL_OLLAMA_ENV,
    EXCEL_OUTPUT_ROOT_ENV,
    EXCEL_PYTHON_ENV,
    EXCEL_ROOT_ENV,
    EXCEL_TIMEOUT_ENV,
)
from coding_agent.integrations.excel_config import ExcelIntegrationConfig

FAKE_CLI_SOURCE = textwrap.dedent(
    r"""
    from __future__ import annotations

    import hashlib
    import json
    import os
    import sys
    import time


    def main() -> int:
        mode = os.environ.get("FAKE_EXCEL_MODE", "success")
        raw = sys.stdin.read()
        request = {}
        if raw.strip():
            try:
                request = json.loads(raw)
            except json.JSONDecodeError:
                request = {}
        request_id = str(request.get("request_id") or "req")
        dump_path = os.environ.get("FAKE_EXCEL_DUMP")
        if dump_path:
            with open(dump_path, "w", encoding="utf-8") as handle:
                json.dump(request, handle, ensure_ascii=False)

        if mode == "sleep":
            time.sleep(float(os.environ.get("FAKE_EXCEL_SLEEP", "30")))
        if mode == "fork":
            pid = os.fork()
            if pid == 0:
                with open(os.environ["FAKE_EXCEL_CHILD_PID"], "w", encoding="utf-8") as handle:
                    handle.write(str(os.getpid()))
                time.sleep(120)
                os._exit(0)
            time.sleep(0.4)
            time.sleep(120)
            return 0
        if mode == "stderr":
            print("diagnostic-line", file=sys.stderr)
            print("SECRET_TOKEN=should-redact", file=sys.stderr)
        if mode == "empty":
            return 0
        if mode == "nonzero_empty":
            print("process crashed", file=sys.stderr)
            return 1
        if mode == "malformed":
            sys.stdout.write("not-json\n")
            print("bad stdout", file=sys.stderr)
            return 0
        if mode == "bad_version":
            payload = _payload(request_id, status="success")
            payload["contract_version"] = "9.9"
            json.dump(payload, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            return 0
        if mode == "unicode":
            payload = _payload(request_id, status="success", text="한글 요약 완료")
            json.dump(payload, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            print("로그: 완료", file=sys.stderr)
            return 0
        if mode == "excel_error":
            payload = _payload(request_id, status="validation_failed", text="검증 실패")
            payload["error"] = {
                "code": "structural_validation_failed",
                "message": "blocked",
                "stage": "structural_validation",
                "retryable": False,
            }
            json.dump(payload, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            return 4
        if mode == "missing_module":
            print("No module named core.application", file=sys.stderr)
            return 1
        if mode in {
            "cannot_plan",
            "model_unavailable",
            "timeout",
            "invalid_request",
            "execution_failed",
            "cancelled",
        }:
            payload = _payload(request_id, status=mode, text=mode)
            json.dump(payload, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            return 0

        payload = _payload(request_id, status="success", text="ok")
        artifacts = os.environ.get("FAKE_EXCEL_ARTIFACTS")
        if artifacts:
            payload["artifacts"] = json.loads(artifacts)
        if os.environ.get("FAKE_EXCEL_WRITE_TRANSFORM"):
            payload["artifacts"] = _write_transform_artifact(request, request_id)
        json.dump(payload, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        print("INFO core.application.cli: done", file=sys.stderr)
        return 0


    def _write_transform_artifact(request, request_id):
        out_dir = str(request.get("output_directory") or "")
        if not out_dir:
            return []
        os.makedirs(out_dir, exist_ok=True)
        dest = os.path.join(out_dir, f"{request_id}_transformed.xlsx")
        data = b"fake-xlsx-bytes"
        with open(dest, "wb") as handle:
            handle.write(data)
        return [
            {
                "artifact_id": "workbook-1",
                "kind": "workbook",
                "path": dest,
                "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "filename": os.path.basename(dest),
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        ]


    def _payload(request_id, status="success", text="ok"):
        return {
            "contract_version": "1.0",
            "request_id": request_id,
            "status": status,
            "text": text,
            "data": {
                "shape": [2, 2],
                "columns": ["항목", "값"],
                "preview_records": [{"항목": "A", "값": 1}],
                "route": "summary",
            },
            "artifacts": [],
            "warnings": [],
            "safety": {"validation_status": "passed", "unsafe_output_blocked": False},
            "timing": {"elapsed_ms": 1},
        }


    if __name__ == "__main__":
        raise SystemExit(main())
    """
).lstrip()


def write_bytes(path: Path, data: bytes = b"excel") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def install_fake_excel_root(root: Path) -> Path:
    package = root / "core" / "application"
    package.mkdir(parents=True, exist_ok=True)
    (root / "core" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "cli.py").write_text(FAKE_CLI_SOURCE, encoding="utf-8")
    return root


def make_executable(path: Path, source: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def clear_excel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        EXCEL_ROOT_ENV,
        EXCEL_PYTHON_ENV,
        EXCEL_TIMEOUT_ENV,
        EXCEL_OUTPUT_ROOT_ENV,
        EXCEL_MODEL_ENV,
        EXCEL_OLLAMA_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


def bind_excel_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    root: Path,
    python: Path,
    timeout: str | None = "180",
    output_root: Path | None = None,
) -> None:
    monkeypatch.setenv(EXCEL_ROOT_ENV, str(root))
    monkeypatch.setenv(EXCEL_PYTHON_ENV, str(python))
    if timeout is None:
        monkeypatch.delenv(EXCEL_TIMEOUT_ENV, raising=False)
    else:
        monkeypatch.setenv(EXCEL_TIMEOUT_ENV, timeout)
    if output_root is not None:
        monkeypatch.setenv(EXCEL_OUTPUT_ROOT_ENV, str(output_root))
    else:
        monkeypatch.delenv(EXCEL_OUTPUT_ROOT_ENV, raising=False)


def fake_config(
    *,
    excel_root: Path,
    workspace: Path,
    python: Path | None = None,
    timeout_seconds: float = 5.0,
) -> ExcelIntegrationConfig:
    return ExcelIntegrationConfig(
        excel_root=excel_root,
        python_executable=python or Path(sys.executable),
        timeout_seconds=timeout_seconds,
        output_root=workspace / "outputs/excel_agent",
        model_name=DEFAULT_EXCEL_MODEL,
        ollama_base_url="http://localhost:11434",
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path
