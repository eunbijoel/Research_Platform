"""Validate Excel artifact manifests against the Coding Agent workspace."""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path
from typing import Any, Sequence

from coding_agent.integrations.excel_errors import (
    ALLOWED_ARTIFACT_KINDS,
    ALLOWED_MEDIA_TYPES,
)
from coding_agent.integrations.excel_paths import path_is_inside, workspace_relative


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_artifacts(
    *,
    workspace: Path,
    artifacts: Any,
    excel_status: str,
    containment_root: Path | None = None,
    source_paths: Sequence[Path] | None = None,
    workbook_only_on_success: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return (verified artifacts, rejection records).

    Invalid artifacts are omitted from the verified list. Excel status is not
    rewritten here.
    """
    verified: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    if artifacts is None:
        return verified, rejected
    if not isinstance(artifacts, list):
        return verified, [{"reason": "artifacts must be a list", "artifact_id": ""}]

    seen_paths: set[Path] = set()
    blocked_sources = {_resolved(path) for path in (source_paths or ()) if path is not None}
    block_workbooks = excel_status == "validation_failed" or (
        workbook_only_on_success and excel_status != "success"
    )
    for index, raw in enumerate(artifacts):
        artifact_id = _artifact_id(raw, index)
        ok, payload, reason = _validate_one(
            workspace=workspace,
            raw=raw,
            index=index,
            seen_paths=seen_paths,
            block_workbooks=block_workbooks,
            containment_root=containment_root,
            blocked_sources=blocked_sources,
        )
        if ok and payload is not None:
            seen_paths.add(Path(payload["path"]))
            verified.append(payload)
        else:
            rejected.append({"artifact_id": artifact_id, "reason": reason})
    return verified, rejected


def _artifact_id(raw: Any, index: int) -> str:
    if isinstance(raw, dict) and raw.get("artifact_id"):
        return str(raw.get("artifact_id"))
    return f"artifact-{index}"


def _validate_one(
    *,
    workspace: Path,
    raw: Any,
    index: int,
    seen_paths: set[Path],
    block_workbooks: bool,
    containment_root: Path | None = None,
    blocked_sources: set[Path] | None = None,
) -> tuple[bool, dict[str, Any] | None, str]:
    if not isinstance(raw, dict):
        return False, None, f"artifacts[{index}] must be an object."
    kind = str(raw.get("kind") or "").strip()
    if kind not in ALLOWED_ARTIFACT_KINDS:
        return False, None, f"Unsupported artifact kind: {kind or 'missing'}."
    if block_workbooks and kind == "workbook":
        return False, None, "Workbook artifacts are blocked for validation_failed responses."

    path_raw = raw.get("path")
    if not isinstance(path_raw, str) or not path_raw.strip():
        return False, None, f"artifacts[{index}].path is missing."
    candidate = Path(path_raw.strip()).expanduser()
    try:
        resolved = candidate.resolve()
    except OSError:
        return False, None, f"artifacts[{index}].path could not be resolved."
    if candidate.is_symlink() or resolved.is_symlink():
        if not path_is_inside(workspace, resolved):
            return False, None, "Artifact symlink escapes the Coding Agent workspace."
        return False, None, "Artifact path must not be a symlink."
    if not path_is_inside(workspace, resolved):
        return False, None, "Artifact path is outside the Coding Agent workspace."
    if containment_root is not None and not path_is_inside(containment_root, resolved):
        return False, None, "Artifact path is outside the request output directory."
    if blocked_sources and resolved in blocked_sources:
        return False, None, "Artifact path must not alias the source workbook."
    if resolved in seen_paths:
        return False, None, "Duplicate artifact path."
    if not resolved.exists():
        return False, None, "Artifact file does not exist."
    try:
        info = resolved.stat()
    except OSError:
        return False, None, "Artifact file is not readable."
    if not stat.S_ISREG(info.st_mode):
        return False, None, "Artifact is not a regular file."

    suffix = resolved.suffix.lower()
    expected_media = ALLOWED_MEDIA_TYPES.get(suffix)
    media_type = str(raw.get("media_type") or "").strip()
    if expected_media is None:
        return False, None, f"Unsupported artifact extension: {suffix or resolved.name!r}."
    if media_type != expected_media:
        return False, None, "Artifact media type does not match the file extension."

    try:
        declared_size = int(raw.get("size_bytes"))
    except (TypeError, ValueError):
        return False, None, "Artifact size_bytes is invalid."
    if declared_size != info.st_size:
        return False, None, "Artifact size does not match the manifest."

    declared_hash = str(raw.get("sha256") or "").strip().lower()
    actual_hash = sha256_file(resolved)
    if not declared_hash or declared_hash != actual_hash:
        return False, None, "Artifact SHA-256 does not match the manifest."

    filename = str(raw.get("filename") or resolved.name).strip() or resolved.name
    return True, {
        "artifact_id": str(raw.get("artifact_id") or f"artifact-{index}"),
        "kind": kind,
        "path": str(resolved),
        "workspace_relative_path": workspace_relative(workspace, resolved),
        "media_type": media_type,
        "filename": filename,
        "size_bytes": info.st_size,
        "sha256": actual_hash,
    }, ""


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path
