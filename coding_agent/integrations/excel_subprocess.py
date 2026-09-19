"""Dedicated Excel Analyzer CLI subprocess client.

Linux process-group lifecycle (this deployment is a Linux server):

- The CLI is started with ``start_new_session=True`` (``os.setsid``), so the
  child is the leader of a new process group.
- On caller timeout the client sends SIGTERM to the process group, waits a
  short grace period, then SIGKILL, then wait/reap.
- Caller timeout is reported as ``subprocess_timeout``. It is never rewritten
  as Excel CLI ``timeout`` or ``cancelled``.

The built-in deepagents ``execute`` shell tool is not used here.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coding_agent.integrations.excel_config import ExcelIntegrationConfig, TRANSFORM_POLICIES
from coding_agent.integrations.excel_errors import (
    DIAGNOSTIC_LIMIT,
    EXCEL_CONTRACT_VERSION,
    EXCEL_STATUSES,
    GRACE_PERIOD_SECONDS,
    TRANSPORT_EXCEL_CLI_UNAVAILABLE,
    TRANSPORT_MALFORMED_RESPONSE,
    TRANSPORT_PROTOCOL_MISMATCH,
    TRANSPORT_SUBPROCESS_FAILED,
    TRANSPORT_SUBPROCESS_TIMEOUT,
)
from coding_agent.integrations.excel_paths import ValidatedInput

_SECRET_LINE = re.compile(
    r"(?i)(api[_-]?key|token|password|secret|authorization|bearer)\s*[:=].+"
)


@dataclass
class ExcelClientResult:
    request_id: str
    transport_error: str | None
    excel_status: str | None
    payload: dict[str, Any] | None
    message: str
    diagnostic: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    pid: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def excel_response(self) -> dict[str, Any] | None:
        return self.payload


def build_analyze_request(
    *,
    config: ExcelIntegrationConfig,
    request_id: str,
    inputs: list[ValidatedInput],
    prompt: str,
    analysis_mode: str,
    profile_name: str,
    output_directory: Path,
) -> dict[str, Any]:
    return {
        "contract_version": EXCEL_CONTRACT_VERSION,
        "request_id": request_id,
        "operation": "analyze",
        "inputs": [
            {
                "source_id": item.source_id,
                "path": str(item.path),
                "sheet": item.sheet,
                "display_name": item.display_name,
            }
            for item in inputs
        ],
        "user_prompt": prompt,
        "analysis_mode": analysis_mode,
        "profile_name": profile_name,
        "model": {
            "base_url": config.ollama_base_url,
            "name": config.model_name,
        },
        "timeout_seconds": config.timeout_seconds,
        "output_directory": str(output_directory.resolve()),
    }


def build_transform_request(
    *,
    config: ExcelIntegrationConfig,
    request_id: str,
    source: ValidatedInput,
    prompt: str,
    output_directory: Path,
) -> dict[str, Any]:
    """Natural-language TransformPromptRequest. Does not emit coordinates."""
    return {
        "contract_version": EXCEL_CONTRACT_VERSION,
        "request_id": request_id,
        "operation": "transform",
        "source": {"path": str(source.path)},
        "user_prompt": prompt,
        "timeout_seconds": config.timeout_seconds,
        "model": {
            "base_url": config.ollama_base_url,
            "name": config.model_name,
        },
        "output_directory": str(Path(output_directory).resolve()),
        "policies": dict(TRANSFORM_POLICIES),
    }


def run_excel_cli(
    config: ExcelIntegrationConfig,
    request: dict[str, Any],
    *,
    timeout_seconds: float | None = None,
) -> ExcelClientResult:
    request_id = str(request.get("request_id") or "")
    timeout = float(config.timeout_seconds if timeout_seconds is None else timeout_seconds)
    command = [str(config.python_executable), "-m", "core.application.cli"]
    env = _subprocess_env(config.excel_root)

    try:
        proc = subprocess.Popen(
            command,
            cwd=str(config.excel_root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            shell=False,
            start_new_session=True,
        )
    except FileNotFoundError:
        return ExcelClientResult(
            request_id=request_id,
            transport_error=TRANSPORT_EXCEL_CLI_UNAVAILABLE,
            excel_status=None,
            payload=None,
            message="Excel Python executable could not be started.",
        )
    except OSError as exc:
        return ExcelClientResult(
            request_id=request_id,
            transport_error=TRANSPORT_EXCEL_CLI_UNAVAILABLE,
            excel_status=None,
            payload=None,
            message="Excel CLI process could not be created.",
            diagnostic=_bound_diagnostic(str(exc)),
        )

    payload_text = json.dumps(request, ensure_ascii=False)
    timed_out = False
    try:
        stdout, stderr = proc.communicate(input=payload_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate_process_group(proc)
        stdout, stderr = _reap_output(proc)
    except OSError as exc:
        terminate_process_group(proc)
        return ExcelClientResult(
            request_id=request_id,
            transport_error=TRANSPORT_SUBPROCESS_FAILED,
            excel_status=None,
            payload=None,
            message="Excel CLI communication failed.",
            diagnostic=_bound_diagnostic(str(exc)),
            pid=proc.pid,
            exit_code=proc.poll(),
        )

    diagnostic = _bound_diagnostic(stderr)
    if timed_out:
        return ExcelClientResult(
            request_id=request_id,
            transport_error=TRANSPORT_SUBPROCESS_TIMEOUT,
            excel_status=None,
            payload=None,
            message=(
                "Excel Analyzer subprocess exceeded the Coding Agent timeout "
                f"({timeout:g}s) and was terminated."
            ),
            diagnostic=diagnostic,
            exit_code=proc.poll(),
            timed_out=True,
            pid=proc.pid,
        )

    exit_code = proc.poll()
    if exit_code is None:
        terminate_process_group(proc)
        exit_code = proc.poll()

    if _cli_unavailable(exit_code, stdout, stderr):
        return ExcelClientResult(
            request_id=request_id,
            transport_error=TRANSPORT_EXCEL_CLI_UNAVAILABLE,
            excel_status=None,
            payload=None,
            message="Excel CLI module is unavailable in the configured environment.",
            diagnostic=diagnostic,
            exit_code=exit_code,
            pid=proc.pid,
        )

    parsed, parse_error = parse_excel_stdout(stdout)
    if parse_error:
        if parse_error == TRANSPORT_MALFORMED_RESPONSE and not (stdout or "").strip() and exit_code not in {0, None}:
            transport = TRANSPORT_SUBPROCESS_FAILED
            message = f"Excel CLI exited {exit_code} without a JSON response."
        else:
            transport = parse_error
            message = _parse_error_message(parse_error, stdout)
        return ExcelClientResult(
            request_id=request_id,
            transport_error=transport,
            excel_status=None,
            payload=None,
            message=message,
            diagnostic=diagnostic,
            exit_code=exit_code,
            pid=proc.pid,
        )

    excel_status = str(parsed.get("status") or "")
    return ExcelClientResult(
        request_id=str(parsed.get("request_id") or request_id),
        transport_error=None,
        excel_status=excel_status,
        payload=parsed,
        message=str(parsed.get("text") or ""),
        diagnostic=diagnostic,
        exit_code=exit_code,
        pid=proc.pid,
    )


def parse_excel_stdout(stdout: str) -> tuple[dict[str, Any] | None, str | None]:
    text = stdout or ""
    stripped = text.strip()
    if not stripped:
        return None, TRANSPORT_MALFORMED_RESPONSE
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None, TRANSPORT_MALFORMED_RESPONSE
    if not isinstance(payload, dict):
        return None, TRANSPORT_MALFORMED_RESPONSE
    version = str(payload.get("contract_version") or "").strip()
    if version != EXCEL_CONTRACT_VERSION:
        return None, TRANSPORT_PROTOCOL_MISMATCH
    status = str(payload.get("status") or "").strip()
    if status not in EXCEL_STATUSES:
        return None, TRANSPORT_PROTOCOL_MISMATCH
    if "request_id" not in payload:
        return None, TRANSPORT_PROTOCOL_MISMATCH
    return payload, None


def terminate_process_group(proc: subprocess.Popen[str], *, grace: float = GRACE_PERIOD_SECONDS) -> None:
    """SIGTERM the process group, then SIGKILL, then reap. Linux-only."""
    if proc.pid is None:
        return
    _signal_group(proc, signal.SIGTERM)
    if _wait(proc, grace):
        return
    _signal_group(proc, signal.SIGKILL)
    _wait(proc, grace)
    if proc.poll() is None:
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass


def _signal_group(proc: subprocess.Popen[str], sig: signal.Signals) -> None:
    if proc.pid is None:
        return
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.send_signal(sig)
        except (ProcessLookupError, OSError):
            return


def _wait(proc: subprocess.Popen[str], timeout: float) -> bool:
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def _reap_output(proc: subprocess.Popen[str]) -> tuple[str, str]:
    try:
        stdout, stderr = proc.communicate(timeout=GRACE_PERIOD_SECONDS)
        return stdout or "", stderr or ""
    except subprocess.TimeoutExpired:
        try:
            stdout, stderr = proc.communicate(timeout=GRACE_PERIOD_SECONDS)
            return stdout or "", stderr or ""
        except (subprocess.TimeoutExpired, OSError):
            return "", ""
    except OSError:
        return "", ""


def _subprocess_env(excel_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    root = str(excel_root)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root if not existing else root + os.pathsep + existing
    return env


def _cli_unavailable(exit_code: int | None, stdout: str, stderr: str) -> bool:
    combined = f"{stdout}\n{stderr}".lower()
    markers = (
        "no module named core",
        "no module named 'core'",
        "no module named core.application",
    )
    if any(marker in combined for marker in markers):
        return True
    if exit_code in {126, 127}:
        return True
    return False


def _parse_error_message(code: str, stdout: str) -> str:
    preview = _bound_text((stdout or "").strip(), limit=120)
    if code == TRANSPORT_PROTOCOL_MISMATCH:
        return "Excel CLI returned a JSON object that does not match contract 1.0."
    if preview:
        return "Excel CLI stdout was not valid contract JSON."
    return "Excel CLI returned empty stdout."


def _bound_diagnostic(text: str | None) -> str:
    cleaned_lines: list[str] = []
    for line in (text or "").splitlines():
        if _SECRET_LINE.search(line):
            cleaned_lines.append("[redacted]")
        else:
            cleaned_lines.append(line)
    return _bound_text("\n".join(cleaned_lines))


def _bound_text(text: str, limit: int = DIAGNOSTIC_LIMIT) -> str:
    compact = text.strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
