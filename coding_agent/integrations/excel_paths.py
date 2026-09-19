"""Workspace containment and Excel tool input validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import stat

from coding_agent.integrations.excel_errors import (
    ALLOWED_ANALYSIS_MODES,
    ALLOWED_INPUT_EXTENSIONS,
    TRANSFORM_ALLOWED_EXTENSIONS,
    TRANSPORT_INVALID_TOOL_INPUT,
    TRANSPORT_WORKSPACE_VIOLATION,
)


@dataclass(frozen=True)
class ValidatedInput:
    source_id: str
    path: Path
    display_name: str
    sheet: str | int
    workspace_relative: str


class PathValidationError(Exception):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def path_is_inside(root: Path, candidate: Path) -> bool:
    try:
        root_resolved = root.resolve()
        candidate_resolved = candidate.resolve()
    except OSError:
        return False
    return candidate_resolved.is_relative_to(root_resolved)


def workspace_relative(workspace: Path, path: Path) -> str:
    return path.resolve().relative_to(workspace.resolve()).as_posix()


def new_request_id() -> str:
    import uuid

    return uuid.uuid4().hex


def prepare_request_output_dir(output_root: Path, workspace: Path, request_id: str) -> Path:
    if not request_id or any(ch in request_id for ch in "/\\"):
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            "Internal request id is not path-safe.",
        )
    try:
        root = output_root.resolve()
        workspace_resolved = workspace.resolve()
    except OSError as exc:
        raise PathValidationError(
            TRANSPORT_WORKSPACE_VIOLATION,
            "Output root could not be resolved.",
        ) from exc
    if not path_is_inside(workspace_resolved, root):
        raise PathValidationError(
            TRANSPORT_WORKSPACE_VIOLATION,
            "Excel output root escaped the Coding Agent workspace.",
        )
    dest = (root / request_id).resolve()
    if not path_is_inside(workspace_resolved, dest) or not path_is_inside(root, dest):
        raise PathValidationError(
            TRANSPORT_WORKSPACE_VIOLATION,
            "Request output directory escaped the Excel output root.",
        )
    if dest.exists():
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            "Request output directory already exists; refusing to overwrite.",
        )
    root.mkdir(parents=True, exist_ok=True)
    return dest


def prepare_transform_output_dir(output_root: Path, workspace: Path, request_id: str) -> Path:
    """Create ``outputs/excel_agent/<request-id>/`` and return that directory.

    Analyze passes the output *root* to Excel Analyzer, which nests
    ``<request-id>/`` itself. Transform passes this directory as
    ``output_directory`` so the executor writes
    ``<request-id>_transformed.xlsx`` directly under it (no extra nesting).
    """
    dest = prepare_request_output_dir(output_root, workspace, request_id)
    dest.mkdir(parents=False, exist_ok=False)
    return dest


def validate_transform_inputs(
    *,
    workspace: Path,
    files: list[str] | None,
    prompt: str | None,
) -> ValidatedInput:
    if not isinstance(prompt, str) or not prompt.strip():
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            "prompt is required and must be a non-empty string.",
        )
    if not isinstance(files, list):
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            "files must be a list of workspace paths.",
        )
    if len(files) != 1:
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            "transform_excel requires exactly one .xlsx file.",
        )
    return _validate_one_file(
        workspace=workspace,
        raw=files[0],
        index=0,
        sheet=0,
        allowed_extensions=TRANSFORM_ALLOWED_EXTENSIONS,
    )


def validate_tool_inputs(
    *,
    workspace: Path,
    files: list[str] | None,
    prompt: str | None,
    analysis_mode: str | None,
    profile_name: str | None,
    sheets: list[Any] | None,
) -> tuple[list[ValidatedInput], str, str]:
    if not isinstance(prompt, str) or not prompt.strip():
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            "prompt is required and must be a non-empty string.",
        )
    mode = str(analysis_mode or "single").strip().lower()
    if mode not in ALLOWED_ANALYSIS_MODES:
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            "analysis_mode must be 'single' or 'multi'.",
        )
    profile = str(profile_name or "generic").strip().lower() or "generic"
    if not isinstance(files, list) or not files:
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            "files must be a non-empty list of workspace paths.",
        )
    if mode == "single" and len(files) != 1:
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            "analysis_mode 'single' requires exactly one file.",
        )
    if mode == "multi" and len(files) < 2:
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            "analysis_mode 'multi' requires at least two files.",
        )
    parsed_sheets = _parse_sheets(sheets, expected=len(files))
    validated: list[ValidatedInput] = []
    for index, raw in enumerate(files):
        validated.append(
            _validate_one_file(
                workspace=workspace,
                raw=raw,
                index=index,
                sheet=parsed_sheets[index],
            )
        )
    ensure_unique_inputs(validated)
    return validated, mode, profile


def ensure_unique_inputs(items: list[ValidatedInput]) -> None:
    seen_paths: set[Path] = set()
    seen_ids: set[str] = set()
    for item in items:
        if item.path in seen_paths:
            raise PathValidationError(
                TRANSPORT_INVALID_TOOL_INPUT,
                f"Duplicate input path: {item.workspace_relative}",
            )
        if item.source_id in seen_ids:
            raise PathValidationError(
                TRANSPORT_INVALID_TOOL_INPUT,
                f"Duplicate source id: {item.source_id}",
            )
        seen_paths.add(item.path)
        seen_ids.add(item.source_id)


def _parse_sheets(sheets: list[Any] | None, *, expected: int) -> list[str | int]:
    if sheets is None:
        return [0] * expected
    if not isinstance(sheets, list):
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            "sheets must be a list matching files, or omitted.",
        )
    if len(sheets) != expected:
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            "sheets length must match files length.",
        )
    parsed: list[str | int] = []
    for index, raw in enumerate(sheets):
        parsed.append(_parse_sheet(raw, index=index))
    return parsed


def _parse_sheet(raw: Any, *, index: int) -> str | int:
    if raw is None:
        return 0
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            f"sheets[{index}] must be a string sheet name or a non-negative integer.",
        )
    if isinstance(raw, int):
        if raw < 0:
            raise PathValidationError(
                TRANSPORT_INVALID_TOOL_INPUT,
                f"sheets[{index}] must be >= 0.",
            )
        return raw
    text = raw.strip()
    if not text:
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            f"sheets[{index}] must be a non-empty string.",
        )
    return text


def _validate_one_file(
    *,
    workspace: Path,
    raw: Any,
    index: int,
    sheet: str | int,
    allowed_extensions: frozenset[str] | None = None,
) -> ValidatedInput:
    if not isinstance(raw, str) or not raw.strip():
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            f"files[{index}] is blank.",
        )
    workspace_resolved = workspace.resolve()
    given = Path(raw.strip()).expanduser()
    candidate = given if given.is_absolute() else (workspace_resolved / given)
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            f"files[{index}] could not be resolved.",
        ) from exc
    if not path_is_inside(workspace_resolved, resolved):
        raise PathValidationError(
            TRANSPORT_WORKSPACE_VIOLATION,
            f"files[{index}] is outside the Coding Agent workspace.",
        )
    if candidate.is_symlink() and not path_is_inside(workspace_resolved, candidate.resolve()):
        raise PathValidationError(
            TRANSPORT_WORKSPACE_VIOLATION,
            f"files[{index}] symlink escapes the Coding Agent workspace.",
        )
    if not resolved.exists():
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            f"files[{index}] does not exist.",
        )
    try:
        mode = resolved.stat().st_mode
    except OSError as exc:
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            f"files[{index}] is not readable.",
        ) from exc
    if not stat.S_ISREG(mode):
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            f"files[{index}] is not a regular file.",
        )
    suffix = resolved.suffix.lower()
    allowed = allowed_extensions if allowed_extensions is not None else ALLOWED_INPUT_EXTENSIONS
    if suffix not in allowed:
        raise PathValidationError(
            TRANSPORT_INVALID_TOOL_INPUT,
            f"Unsupported file extension: {suffix or resolved.name!r}.",
        )
    source_id = f"file-{index + 1}"
    return ValidatedInput(
        source_id=source_id,
        path=resolved,
        display_name=resolved.name,
        sheet=sheet,
        workspace_relative=workspace_relative(workspace_resolved, resolved),
    )
