from __future__ import annotations

from pathlib import Path

from coding_agent.config import (
    DEFAULT_EXCEL_MODEL,
    EXCEL_OUTPUT_ROOT_ENV,
    EXCEL_PYTHON_ENV,
    EXCEL_ROOT_ENV,
    EXCEL_TIMEOUT_ENV,
)
from coding_agent.integrations.excel_config import load_excel_config
from coding_agent.tests.conftest import bind_excel_env, clear_excel_env, install_fake_excel_root


def test_explicit_valid_config(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    python = Path(__import__("sys").executable)
    bind_excel_env(monkeypatch, root=root, python=python, timeout="90")
    monkeypatch.setenv("CODING_AGENT_EXCEL_MODEL", DEFAULT_EXCEL_MODEL)
    monkeypatch.setenv("CODING_AGENT_EXCEL_OLLAMA", "http://localhost:11434")
    result = load_excel_config(workspace)
    assert result.ok
    assert result.config is not None
    assert result.config.excel_root == root.resolve()
    assert result.config.python_executable == python
    assert result.config.timeout_seconds == 90
    assert result.config.output_root == (workspace / "outputs/excel_agent").resolve()
    assert result.config.model_name == DEFAULT_EXCEL_MODEL


def test_missing_excel_root(monkeypatch, workspace: Path) -> None:
    clear_excel_env(monkeypatch)
    monkeypatch.setenv(EXCEL_ROOT_ENV, str(workspace / "missing-excel"))
    monkeypatch.setenv(EXCEL_PYTHON_ENV, str(Path(__import__("sys").executable)))
    result = load_excel_config(workspace)
    assert not result.ok
    assert result.error_code == "configuration_error"
    assert "does not exist" in result.message


def test_missing_python_executable(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    bind_excel_env(monkeypatch, root=root, python=tmp_path / "no-python")
    result = load_excel_config(workspace)
    assert not result.ok
    assert "Python executable does not exist" in result.message


def test_missing_cli_module(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = tmp_path / "excel"
    root.mkdir()
    python = Path(__import__("sys").executable)
    bind_excel_env(monkeypatch, root=root, python=python)
    result = load_excel_config(workspace)
    assert not result.ok
    assert "CLI module is missing" in result.message


def test_invalid_timeout(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    python = Path(__import__("sys").executable)
    bind_excel_env(monkeypatch, root=root, python=python, timeout="0")
    result = load_excel_config(workspace)
    assert not result.ok
    assert EXCEL_TIMEOUT_ENV in result.message

    bind_excel_env(monkeypatch, root=root, python=python, timeout="not-a-number")
    result = load_excel_config(workspace)
    assert not result.ok

    bind_excel_env(monkeypatch, root=root, python=python, timeout="999999")
    result = load_excel_config(workspace)
    assert not result.ok


def test_output_root_outside_workspace(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    python = Path(__import__("sys").executable)
    bind_excel_env(
        monkeypatch,
        root=root,
        python=python,
        output_root=tmp_path / "outside-output",
    )
    result = load_excel_config(workspace)
    assert not result.ok
    assert "inside the Coding Agent workspace" in result.message


def test_output_root_symlink_escape(monkeypatch, tmp_path: Path, workspace: Path) -> None:
    root = install_fake_excel_root(tmp_path / "excel")
    python = Path(__import__("sys").executable)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace / "escaped-output"
    link.symlink_to(outside)
    bind_excel_env(monkeypatch, root=root, python=python, output_root=link)
    result = load_excel_config(workspace)
    assert not result.ok
    assert "workspace" in result.message.lower() or "symlink" in result.message.lower()


def test_config_does_not_use_coding_agent_sys_executable_implicitly(
    monkeypatch, tmp_path: Path, workspace: Path
) -> None:
    import sys

    root = install_fake_excel_root(tmp_path / "excel")
    monkeypatch.setenv(EXCEL_ROOT_ENV, str(root))
    monkeypatch.delenv(EXCEL_PYTHON_ENV, raising=False)
    monkeypatch.delenv(EXCEL_OUTPUT_ROOT_ENV, raising=False)
    monkeypatch.delenv(EXCEL_TIMEOUT_ENV, raising=False)
    result = load_excel_config(workspace)
    assert not result.ok
    assert "Python executable does not exist" in result.message
    default_python = root / ".venv" / "bin" / "python"
    assert str(default_python) in result.message
    assert Path(sys.executable).resolve() != default_python.resolve()


def test_venv_python_symlink_is_kept_as_launcher(
    monkeypatch, tmp_path: Path, workspace: Path
) -> None:
    import sys

    root = install_fake_excel_root(tmp_path / "excel")
    launcher = tmp_path / "excel-venv" / "bin" / "python"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(sys.executable)
    bind_excel_env(monkeypatch, root=root, python=launcher, timeout="30")
    result = load_excel_config(workspace)
    assert result.ok
    assert result.config is not None
    assert result.config.python_executable == launcher.absolute()
    assert result.config.python_executable != Path(sys.executable).resolve() or launcher.is_symlink()
