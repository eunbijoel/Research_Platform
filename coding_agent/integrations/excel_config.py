"""Load Excel Analyzer subprocess settings without importing that package.

Configuration errors are returned as structured values. They must not raise
during `import coding_agent` or Streamlit app import.
"""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from coding_agent.config import (
    DEFAULT_EXCEL_MODEL,
    DEFAULT_EXCEL_OLLAMA,
    DEFAULT_EXCEL_OUTPUT_DIRNAME,
    DEFAULT_EXCEL_TIMEOUT_SECONDS,
    EXCEL_CLI_MODULE_RELATIVE,
    EXCEL_MODEL_ENV,
    EXCEL_OLLAMA_ENV,
    EXCEL_OUTPUT_ROOT_ENV,
    EXCEL_PYTHON_ENV,
    EXCEL_ROOT_ENV,
    EXCEL_TIMEOUT_ENV,
    MAX_EXCEL_TIMEOUT_SECONDS,
    MIN_EXCEL_TIMEOUT_SECONDS,
    ROOT,
)
from coding_agent.integrations.excel_errors import TRANSPORT_CONFIGURATION_ERROR
from coding_agent.integrations.excel_paths import path_is_inside

# Product-approved Transform v1 policies. Not inferred from the user prompt.
TRANSFORM_POLICIES: dict[str, str] = {
    "group_scope": "include_descendants_and_subtotals",
    "output_mode": "copy_with_new_sheet",
    "unmerge_fill_policy": "fill_all",
}


@dataclass(frozen=True)
class ExcelIntegrationConfig:
    excel_root: Path
    python_executable: Path
    timeout_seconds: float
    output_root: Path
    model_name: str
    ollama_base_url: str


@dataclass(frozen=True)
class ExcelConfigResult:
    config: ExcelIntegrationConfig | None
    error_code: str | None
    message: str

    @property
    def ok(self) -> bool:
        return self.config is not None and self.error_code is None


def sibling_excel_root() -> Path:
    """Prefer a sibling of this repo; fall back to the home workspace.

    research-memory lives on /mnt/data, while excel_ai_analyzer is next to
    /home/eunbi. Keep the standalone coding-agent layout as the first candidate.
    """
    candidates = [
        ROOT.parent / "excel_ai_analyzer",
        Path("/home/eunbi/excel_ai_analyzer"),
    ]
    for raw in candidates:
        path = raw.expanduser().resolve()
        if (path / EXCEL_CLI_MODULE_RELATIVE).is_file():
            return path
    return candidates[0].expanduser().resolve()


def default_excel_python(excel_root: Path) -> Path:
    return excel_root / ".venv" / "bin" / "python"


def load_excel_config(workspace: Path) -> ExcelConfigResult:
    """Resolve and validate Excel integration settings for `workspace`."""
    try:
        workspace_resolved = Path(workspace).expanduser().resolve()
    except OSError:
        return _config_error("Workspace path could not be resolved.")
    if not workspace_resolved.is_dir():
        return _config_error("Workspace is not a directory.")

    timeout_result = _parse_timeout(os.environ.get(EXCEL_TIMEOUT_ENV))
    if isinstance(timeout_result, str):
        return _config_error(timeout_result)

    root_raw = os.environ.get(EXCEL_ROOT_ENV, "").strip()
    excel_root = Path(root_raw).expanduser() if root_raw else sibling_excel_root()
    root_error = _validate_excel_root(excel_root)
    if root_error:
        return _config_error(root_error)

    python_raw = os.environ.get(EXCEL_PYTHON_ENV, "").strip()
    python_path, python_error = _resolve_python(
        python_raw, default_excel_python(excel_root)
    )
    if python_error:
        return _config_error(python_error)

    output_result = _resolve_output_root(workspace_resolved)
    if isinstance(output_result, str):
        return _config_error(output_result)

    model_name = os.environ.get(EXCEL_MODEL_ENV, "").strip() or DEFAULT_EXCEL_MODEL
    ollama = os.environ.get(EXCEL_OLLAMA_ENV, "").strip() or DEFAULT_EXCEL_OLLAMA
    if not model_name:
        return _config_error("Excel model name is empty.")
    if not ollama:
        return _config_error("Excel Ollama base URL is empty.")

    return ExcelConfigResult(
        config=ExcelIntegrationConfig(
            excel_root=excel_root.resolve(),
            python_executable=python_path,
            timeout_seconds=timeout_result,
            output_root=output_result,
            model_name=model_name,
            ollama_base_url=ollama.rstrip("/"),
        ),
        error_code=None,
        message="",
    )


def _config_error(message: str) -> ExcelConfigResult:
    return ExcelConfigResult(
        config=None,
        error_code=TRANSPORT_CONFIGURATION_ERROR,
        message=message,
    )


def _parse_timeout(raw: str | None) -> float | str:
    if raw is None or str(raw).strip() == "":
        return DEFAULT_EXCEL_TIMEOUT_SECONDS
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return f"{EXCEL_TIMEOUT_ENV} must be a positive number."
    if value != value or value in {float("inf"), float("-inf")}:
        return f"{EXCEL_TIMEOUT_ENV} must be a finite number."
    if value < MIN_EXCEL_TIMEOUT_SECONDS or value > MAX_EXCEL_TIMEOUT_SECONDS:
        return (
            f"{EXCEL_TIMEOUT_ENV} must be between "
            f"{MIN_EXCEL_TIMEOUT_SECONDS:g} and {MAX_EXCEL_TIMEOUT_SECONDS:g} seconds."
        )
    return value


def _validate_excel_root(excel_root: Path) -> str | None:
    try:
        resolved = excel_root.expanduser().resolve()
    except OSError:
        return f"{EXCEL_ROOT_ENV} could not be resolved: {excel_root}"
    if not resolved.is_dir():
        return f"Excel analyzer root does not exist or is not a directory: {resolved}"
    cli_path = resolved / EXCEL_CLI_MODULE_RELATIVE
    if not cli_path.is_file():
        return (
            "Excel CLI module is missing: "
            f"{cli_path} (expected python -m core.application.cli)."
        )
    return None


def _resolve_python(raw: str, default: Path) -> tuple[Path, str | None]:
    candidate = Path(raw).expanduser() if raw else default
    located, error = _validate_python(candidate)
    return located, error


def _validate_python(python_path: Path) -> tuple[Path, str | None]:
    try:
        candidate = python_path.expanduser()
    except OSError:
        return python_path, f"{EXCEL_PYTHON_ENV} could not be resolved: {python_path}"

    if not candidate.exists() and not candidate.is_absolute() and len(candidate.parts) == 1:
        found = shutil.which(str(candidate))
        if found:
            candidate = Path(found)

    if not candidate.is_absolute():
        candidate = candidate.absolute()

    if not candidate.exists():
        return candidate, f"Excel Python executable does not exist: {candidate}"
    try:
        mode = candidate.stat().st_mode
    except OSError:
        return candidate, f"Excel Python executable is not readable: {candidate}"
    if not stat.S_ISREG(mode):
        return candidate, f"Excel Python executable is not a regular file: {candidate}"
    if not os.access(candidate, os.X_OK):
        return candidate, f"Excel Python executable is not executable: {candidate}"
    # Keep the launcher path. Resolving a venv `bin/python` symlink would run
    # the system interpreter without Excel's site-packages.
    return candidate, None


def _resolve_output_root(workspace: Path) -> Path | str:
    raw = os.environ.get(EXCEL_OUTPUT_ROOT_ENV, "").strip()
    if not raw:
        candidate = workspace / DEFAULT_EXCEL_OUTPUT_DIRNAME
    else:
        path = Path(raw).expanduser()
        candidate = path if path.is_absolute() else (workspace / path)
    try:
        resolved = candidate.resolve()
    except OSError:
        return "Excel output root could not be resolved."
    if not path_is_inside(workspace, resolved):
        return "Excel output root must stay inside the Coding Agent workspace."
    if resolved.exists() and not resolved.is_dir():
        return "Excel output root exists and is not a directory."
    if candidate.is_symlink() and not path_is_inside(workspace, candidate.resolve()):
        return "Excel output root symlink escapes the Coding Agent workspace."
    return resolved
