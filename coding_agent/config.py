from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKSPACE = ROOT / "data" / "coding_agent" / "workspace"
DATA_DIR = Path(os.environ.get("CODING_AGENT_DATA", ROOT / "data" / "coding_agent")).expanduser()

OLLAMA_HOST = os.environ.get("CODING_AGENT_OLLAMA", "http://127.0.0.1:11434").rstrip("/")
# Bare Ollama tag; bridge prefixes with ollama:
MODEL_NAME = os.environ.get("CODING_AGENT_MODEL", "gemma4:31b")
MAX_TOOL_ROUNDS = int(os.environ.get("CODING_AGENT_MAX_ROUNDS", "12"))
MAX_FILE_CHARS = int(os.environ.get("CODING_AGENT_MAX_FILE_CHARS", "24000"))

# Excel Analyzer subprocess integration (validated at tool-call time, not import).
EXCEL_ROOT_ENV = "CODING_AGENT_EXCEL_ROOT"
EXCEL_PYTHON_ENV = "CODING_AGENT_EXCEL_PYTHON"
EXCEL_TIMEOUT_ENV = "CODING_AGENT_EXCEL_TIMEOUT"
EXCEL_OUTPUT_ROOT_ENV = "CODING_AGENT_EXCEL_OUTPUT_ROOT"
EXCEL_MODEL_ENV = "CODING_AGENT_EXCEL_MODEL"
EXCEL_OLLAMA_ENV = "CODING_AGENT_EXCEL_OLLAMA"
DEFAULT_EXCEL_TIMEOUT_SECONDS = 180.0
MIN_EXCEL_TIMEOUT_SECONDS = 1.0
MAX_EXCEL_TIMEOUT_SECONDS = 86_400.0
# Visible under Files explorer (dot-dirs are hidden). Unique request subdirs refuse overwrite.
DEFAULT_EXCEL_OUTPUT_DIRNAME = "outputs/excel_agent"
# Same default Ollama tag as the coding agent unless CODING_AGENT_EXCEL_MODEL is set.
DEFAULT_EXCEL_MODEL = MODEL_NAME
DEFAULT_EXCEL_OLLAMA = "http://localhost:11434"
EXCEL_CLI_MODULE_RELATIVE = Path("core") / "application" / "cli.py"

SHELL_DENY = (
    "rm -rf /",
    "mkfs",
    ":(){",
    "shutdown",
    "reboot",
    "dd if=",
    "> /dev/",
)

IGNORE_DIR_NAMES = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".deepagents",
    ".streamlit",
    ".session_uploads",
    "data",
}
IGNORE_SUFFIXES = {".pyc", ".pyo", ".pyd", ".so", ".dll", ".egg"}


def resolve_workspace(path: str | Path | None = None) -> Path:
    raw = path or os.environ.get("CODING_AGENT_WORKSPACE") or DEFAULT_WORKSPACE
    ws = Path(raw).expanduser().resolve()
    ws.mkdir(parents=True, exist_ok=True)
    return ws
