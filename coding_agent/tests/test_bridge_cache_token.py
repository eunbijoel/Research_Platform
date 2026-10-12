"""Cache token must invalidate Streamlit bridges when Excel modules change."""

from __future__ import annotations

from coding_agent import ui


def test_bridge_cache_token_includes_excel_modules() -> None:
    token = ui._bridge_cache_token()
    assert "excel-hitl-v1" in token
    assert "excel_tool.py" in token
    assert "bridge.py" in token
    assert "spreadsheet.py" in token
    assert "excel_subprocess.py" in token or "excel_config.py" in token
