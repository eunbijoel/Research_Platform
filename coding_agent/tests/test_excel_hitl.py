from __future__ import annotations

import inspect

from deepagents_code.agent import _add_interrupt_on, create_cli_agent

from coding_agent.bridge import interrupt_on_with_excel
from coding_agent.tools.excel_tool import ANALYZE_EXCEL_NAME, TRANSFORM_EXCEL_NAME


def test_create_cli_agent_public_signature_has_tools_not_interrupt_on() -> None:
    params = inspect.signature(create_cli_agent).parameters
    assert "tools" in params
    assert "mcp_tools" in params
    assert "interrupt_on" not in params


def test_stock_interrupt_map_excludes_excel_tools() -> None:
    mapping = _add_interrupt_on()
    assert "execute" in mapping
    assert "write_file" in mapping
    assert ANALYZE_EXCEL_NAME not in mapping
    assert TRANSFORM_EXCEL_NAME not in mapping


def test_interrupt_on_with_excel_gates_analyze_and_transform() -> None:
    mapping = interrupt_on_with_excel()
    assert "execute" in mapping
    assert "write_file" in mapping
    assert ANALYZE_EXCEL_NAME in mapping
    assert TRANSFORM_EXCEL_NAME in mapping
    for name in (ANALYZE_EXCEL_NAME, TRANSFORM_EXCEL_NAME):
        cfg = mapping[name]
        assert cfg["allowed_decisions"] == ["approve", "reject"]
        assert callable(cfg["description"])
        assert callable(cfg["when"])
