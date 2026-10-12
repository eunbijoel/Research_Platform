"""Tests for workspace file/folder delete."""

from __future__ import annotations

from pathlib import Path

from coding_agent.workbench import delete_workspace_file


def test_delete_file(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hi", encoding="utf-8")
    ok, err = delete_workspace_file(tmp_path, "a.txt")
    assert ok and err is None
    assert not f.exists()


def test_delete_folder_recursive(tmp_path: Path) -> None:
    d = tmp_path / "pkg"
    nested = d / "sub"
    nested.mkdir(parents=True)
    (nested / "x.py").write_text("print(1)\n", encoding="utf-8")
    (d / "readme.md").write_text("hi\n", encoding="utf-8")
    ok, err = delete_workspace_file(tmp_path, "pkg")
    assert ok and err is None
    assert not d.exists()


def test_refuse_workspace_root(tmp_path: Path) -> None:
    ok, err = delete_workspace_file(tmp_path, "")
    assert not ok
    assert err
