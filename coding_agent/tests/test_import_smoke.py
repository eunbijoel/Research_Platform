from __future__ import annotations

from pathlib import Path


def test_import_coding_agent() -> None:
    import coding_agent

    assert coding_agent.__version__


def test_import_bridge_and_construct(tmp_path: Path) -> None:
    from coding_agent.bridge import DeepAgentsBridge

    workspace = tmp_path / "ws"
    workspace.mkdir()
    bridge = DeepAgentsBridge(workspace=workspace, data_dir=tmp_path / "data")
    assert bridge.workspace == workspace.resolve()
    assert bridge._agent is None


def test_import_ui_module() -> None:
    import coding_agent.ui as ui_mod

    assert hasattr(ui_mod, "run_coding_agent_app")
    assert hasattr(ui_mod, "_bridge")
    assert hasattr(ui_mod, "_bridge_cache_token")
