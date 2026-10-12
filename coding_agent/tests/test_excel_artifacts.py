from __future__ import annotations

import hashlib
from pathlib import Path

from coding_agent.integrations.excel_artifacts import sha256_file, validate_artifacts
from coding_agent.tests.conftest import write_bytes

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _manifest(path: Path, *, kind: str = "table", artifact_id: str = "a1", size=None, digest=None):
    data = path.read_bytes()
    return {
        "artifact_id": artifact_id,
        "kind": kind,
        "path": str(path.resolve()),
        "media_type": XLSX_MEDIA,
        "filename": path.name,
        "size_bytes": len(data) if size is None else size,
        "sha256": hashlib.sha256(data).hexdigest() if digest is None else digest,
    }


def test_valid_artifact(workspace: Path) -> None:
    path = write_bytes(workspace / "outputs/excel_agent" / "req" / "out.xlsx", b"abc")
    verified, rejected = validate_artifacts(
        workspace=workspace,
        artifacts=[_manifest(path)],
        excel_status="success",
    )
    assert rejected == []
    assert verified[0]["workspace_relative_path"] == "outputs/excel_agent/req/out.xlsx"
    assert verified[0]["sha256"] == sha256_file(path)


def test_missing_artifact(workspace: Path) -> None:
    missing = workspace / "outputs/excel_agent" / "req" / "gone.xlsx"
    payload = _manifest(write_bytes(workspace / "tmp.xlsx"))
    payload["path"] = str(missing)
    verified, rejected = validate_artifacts(
        workspace=workspace, artifacts=[payload], excel_status="success"
    )
    assert verified == []
    assert rejected and "does not exist" in rejected[0]["reason"]


def test_outside_workspace_artifact(workspace: Path, tmp_path: Path) -> None:
    outsider = write_bytes(tmp_path / "out.xlsx")
    verified, rejected = validate_artifacts(
        workspace=workspace, artifacts=[_manifest(outsider)], excel_status="success"
    )
    assert verified == []
    assert "outside" in rejected[0]["reason"]


def test_symlink_artifact(workspace: Path, tmp_path: Path) -> None:
    target = write_bytes(tmp_path / "out.xlsx")
    link = workspace / "link.xlsx"
    link.symlink_to(target)
    verified, rejected = validate_artifacts(
        workspace=workspace, artifacts=[_manifest(link)], excel_status="success"
    )
    assert verified == []
    assert "symlink" in rejected[0]["reason"] or "outside" in rejected[0]["reason"]


def test_size_mismatch(workspace: Path) -> None:
    path = write_bytes(workspace / "out.xlsx", b"abc")
    verified, rejected = validate_artifacts(
        workspace=workspace,
        artifacts=[_manifest(path, size=99)],
        excel_status="success",
    )
    assert verified == []
    assert "size" in rejected[0]["reason"]


def test_sha256_mismatch(workspace: Path) -> None:
    path = write_bytes(workspace / "out.xlsx", b"abc")
    verified, rejected = validate_artifacts(
        workspace=workspace,
        artifacts=[_manifest(path, digest="0" * 64)],
        excel_status="success",
    )
    assert verified == []
    assert "SHA-256" in rejected[0]["reason"]


def test_duplicate_artifact(workspace: Path) -> None:
    path = write_bytes(workspace / "out.xlsx", b"abc")
    verified, rejected = validate_artifacts(
        workspace=workspace,
        artifacts=[_manifest(path, artifact_id="a"), _manifest(path, artifact_id="b")],
        excel_status="success",
    )
    assert len(verified) == 1
    assert rejected and "Duplicate" in rejected[0]["reason"]


def test_validation_failure_workbook_blocked(workspace: Path) -> None:
    path = write_bytes(workspace / "bad.xlsx", b"abc")
    verified, rejected = validate_artifacts(
        workspace=workspace,
        artifacts=[_manifest(path, kind="workbook")],
        excel_status="validation_failed",
    )
    assert verified == []
    assert "Workbook artifacts are blocked" in rejected[0]["reason"]


def test_request_dir_containment_and_source_alias(workspace: Path) -> None:
    request_dir = workspace / "outputs/excel_agent" / "req"
    inside = write_bytes(request_dir / "out.xlsx", b"abc")
    outside = write_bytes(workspace / "other.xlsx", b"abc")
    source = write_bytes(workspace / "source.xlsx", b"abc")
    verified, rejected = validate_artifacts(
        workspace=workspace,
        artifacts=[_manifest(inside, kind="workbook")],
        excel_status="success",
        containment_root=request_dir,
        source_paths=[source],
        workbook_only_on_success=True,
    )
    assert rejected == []
    assert verified
    verified, rejected = validate_artifacts(
        workspace=workspace,
        artifacts=[_manifest(outside, kind="workbook")],
        excel_status="success",
        containment_root=request_dir,
        workbook_only_on_success=True,
    )
    assert verified == []
    assert "request output directory" in rejected[0]["reason"]
    verified, rejected = validate_artifacts(
        workspace=workspace,
        artifacts=[_manifest(source, kind="workbook")],
        excel_status="success",
        containment_root=workspace,
        source_paths=[source],
        workbook_only_on_success=True,
    )
    assert verified == []
    assert "source" in rejected[0]["reason"]
    verified, rejected = validate_artifacts(
        workspace=workspace,
        artifacts=[_manifest(inside, kind="workbook")],
        excel_status="cannot_plan",
        workbook_only_on_success=True,
    )
    assert verified == []
    assert "Workbook artifacts are blocked" in rejected[0]["reason"]
