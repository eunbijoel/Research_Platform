from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from coding_agent.bridge import DeepAgentsBridge, workspace_agent_tools
from coding_agent.tools.excel_tool import ANALYZE_EXCEL_NAME, TRANSFORM_EXCEL_NAME

REQUIRED_TOOL_NAMES = {
    "inspect_spreadsheet",
    "read_spreadsheet",
    ANALYZE_EXCEL_NAME,
    TRANSFORM_EXCEL_NAME,
}


def _tool_names(tools) -> list[str]:
    return [getattr(tool, "name", None) for tool in tools]


def _assert_combined_tools(tools) -> None:
    names = _tool_names(tools)
    assert names.count("inspect_spreadsheet") == 1
    assert names.count("read_spreadsheet") == 1
    assert names.count(ANALYZE_EXCEL_NAME) == 1
    assert names.count(TRANSFORM_EXCEL_NAME) == 1
    assert len(names) == len(set(names))
    assert REQUIRED_TOOL_NAMES <= set(names)


def test_create_cli_agent_receives_combined_tools(monkeypatch, tmp_path: Path) -> None:
    captured: list[dict] = []
    hitl_maps: list[dict] = []

    def fake_create_cli_agent(**kwargs):
        import deepagents_code.agent as da

        hitl_maps.append(da._add_interrupt_on())
        captured.append(kwargs)
        return MagicMock(name="agent"), MagicMock(name="backend")

    monkeypatch.setattr("coding_agent.bridge.create_cli_agent", fake_create_cli_agent)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    bridge = DeepAgentsBridge(workspace=workspace, data_dir=tmp_path / "data", model="ollama:gemma4:31b")
    _ = bridge.agent
    assert len(captured) == 1
    kwargs = captured[0]
    _assert_combined_tools(kwargs["tools"])
    assert kwargs["enable_shell"] is True
    assert kwargs["model"] == bridge.model
    assert kwargs["cwd"] == bridge.workspace
    assert kwargs["checkpointer"] is bridge._checkpointer
    assert kwargs["interactive"] is False
    assert kwargs["enable_memory"] is False
    assert hitl_maps and ANALYZE_EXCEL_NAME in hitl_maps[0]
    assert TRANSFORM_EXCEL_NAME in hitl_maps[0]


def test_existing_flags_preserved_and_reset_reregisters(monkeypatch, tmp_path: Path) -> None:
    captured: list[dict] = []

    def fake_create_cli_agent(**kwargs):
        captured.append(kwargs)
        return MagicMock(name="agent"), MagicMock(name="backend")

    monkeypatch.setattr("coding_agent.bridge.create_cli_agent", fake_create_cli_agent)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    bridge = DeepAgentsBridge(
        workspace=workspace,
        data_dir=tmp_path / "data",
        model="ollama:gemma4:31b",
        auto_approve=False,
    )
    first = bridge.agent
    second = bridge.agent
    assert first is second
    assert len(captured) == 1
    bridge.reset_agent()
    _ = bridge.agent
    assert len(captured) == 2
    for kwargs in captured:
        assert kwargs["enable_shell"] is True
        _assert_combined_tools(kwargs["tools"])
        assert kwargs["auto_approve"] is False
        assert kwargs["enable_ask_user"] is False
        assert kwargs["enable_skills"] is False


def test_new_bridge_instance_keeps_combined_tools(monkeypatch, tmp_path: Path) -> None:
    captured: list[dict] = []

    def fake_create_cli_agent(**kwargs):
        captured.append(kwargs)
        return MagicMock(name="agent"), MagicMock(name="backend")

    monkeypatch.setattr("coding_agent.bridge.create_cli_agent", fake_create_cli_agent)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    first = DeepAgentsBridge(workspace=workspace, data_dir=tmp_path / "data1")
    second = DeepAgentsBridge(workspace=workspace, data_dir=tmp_path / "data2")
    _ = first.agent
    _ = second.agent
    assert len(captured) == 2
    for kwargs in captured:
        assert kwargs["enable_shell"] is True
        _assert_combined_tools(kwargs["tools"])


def test_workspace_agent_tools_has_no_duplicate_names(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    tools = workspace_agent_tools(workspace)
    _assert_combined_tools(tools)


def test_excel_configuration_error_does_not_kill_import(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CODING_AGENT_EXCEL_ROOT", str(tmp_path / "missing"))
    import coding_agent
    import coding_agent.bridge as bridge_mod

    assert coding_agent.__version__
    workspace = tmp_path / "ws"
    workspace.mkdir()
    DeepAgentsBridge(workspace=workspace, data_dir=tmp_path / "data")
    assert bridge_mod.DeepAgentsBridge is DeepAgentsBridge


def test_excel_configuration_error_does_not_drop_inspect_read(monkeypatch, tmp_path: Path) -> None:
    captured: list[dict] = []

    def fake_create_cli_agent(**kwargs):
        captured.append(kwargs)
        return MagicMock(name="agent"), MagicMock(name="backend")

    monkeypatch.setattr("coding_agent.bridge.create_cli_agent", fake_create_cli_agent)
    monkeypatch.setenv("CODING_AGENT_EXCEL_ROOT", str(tmp_path / "missing"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "sample.csv").write_text("item,value\nA,1\n", encoding="utf-8")
    bridge = DeepAgentsBridge(workspace=workspace, data_dir=tmp_path / "data")
    _ = bridge.agent
    tools = captured[0]["tools"]
    _assert_combined_tools(tools)
    inspect = next(tool for tool in tools if tool.name == "inspect_spreadsheet")
    payload = json.loads(inspect.invoke({"path": "sample.csv"}))
    assert "error" not in payload
    assert payload["sheets"][0]["column_names"] == ["item", "value"]
