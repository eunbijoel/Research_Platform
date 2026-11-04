"""Tests for spreadsheet upload/inspect/read safety."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from coding_agent.spreadsheet import (
    MAX_READ_ROWS,
    MAX_UPLOAD_BYTES,
    inspect_spreadsheet_data,
    make_spreadsheet_tools,
    read_spreadsheet_data,
    resolve_under_workspace,
    sanitize_filename,
    save_upload,
)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "uploads").mkdir()
    (tmp_path / "outputs").mkdir()
    return tmp_path


def _sample_xlsx(path: Path) -> None:
    df = pd.DataFrame(
        {
            "월": ["2024-01", "2024-01", "2024-02"],
            "제품": ["A", "B", "A"],
            "매출": [1000, 2500, 1800],
            "날짜": pd.to_datetime(["2024-01-05", "2024-01-20", "2024-02-03"]),
        }
    )
    df.to_excel(path, index=False, sheet_name="sales")


def test_sanitize_blocks_path_traversal() -> None:
    with pytest.raises(ValueError):
        sanitize_filename("/etc/passwd.xlsx")
    with pytest.raises(ValueError):
        sanitize_filename("notes.txt")
    # Path segments are stripped to a safe basename
    assert sanitize_filename("../secret.xlsx") == "secret.xlsx"
    assert sanitize_filename("foo/bar.xlsx") == "bar.xlsx"
    assert sanitize_filename("sales.xlsx") == "sales.xlsx"


def test_save_upload_xlsx_and_csv(workspace: Path) -> None:
    src = workspace / "tmp.xlsx"
    _sample_xlsx(src)
    data = src.read_bytes()
    result = save_upload(workspace, filename="sample_sales.xlsx", data=data)
    assert result["path"] == "uploads/sample_sales.xlsx"
    assert (workspace / result["path"]).is_file()

    csv_data = "월,제품,매출\n2024-01,가나다,100\n".encode("utf-8")
    csv_result = save_upload(workspace, filename="sample.csv", data=csv_data)
    assert csv_result["path"] == "uploads/sample.csv"


def test_unique_name_when_exists(workspace: Path) -> None:
    data = b"a,b\n1,2\n"
    first = save_upload(workspace, filename="dup.csv", data=data)
    second = save_upload(workspace, filename="dup.csv", data=data, overwrite=False)
    assert first["path"] == "uploads/dup.csv"
    assert second["path"] == "uploads/dup_1.csv"


def test_reject_oversized_upload(workspace: Path) -> None:
    with pytest.raises(ValueError, match="too large"):
        save_upload(workspace, filename="big.csv", data=b"x" * (MAX_UPLOAD_BYTES + 1))


def test_inspect_and_read_xlsx(workspace: Path) -> None:
    path = workspace / "uploads" / "sales.xlsx"
    _sample_xlsx(path)
    info = inspect_spreadsheet_data(workspace, "uploads/sales.xlsx")
    assert info["file"] == "sales.xlsx"
    assert info["sheets"][0]["name"] == "sales"
    assert "월" in info["sheets"][0]["column_names"]
    assert info["sheets"][0]["rows"] == 3

    rows = read_spreadsheet_data(
        workspace,
        "uploads/sales.xlsx",
        sheet="sales",
        columns=["제품", "매출"],
        max_rows=2,
    )
    assert rows["row_count"] == 2
    assert rows["truncated"] is True
    assert rows["columns"] == ["제품", "매출"]


def test_csv_korean_encoding(workspace: Path) -> None:
    path = workspace / "uploads" / "ko.csv"
    path.write_bytes("제품,매출\n한글제품,1234\n".encode("cp949"))
    info = inspect_spreadsheet_data(workspace, "uploads/ko.csv")
    assert "제품" in info["sheets"][0]["column_names"]


def test_workspace_escape_blocked(workspace: Path) -> None:
    with pytest.raises(PermissionError):
        resolve_under_workspace(workspace, "../outside.csv")
    with pytest.raises(PermissionError):
        resolve_under_workspace(workspace, "/etc/passwd")


def test_max_rows_cap(workspace: Path) -> None:
    path = workspace / "uploads" / "many.csv"
    lines = ["n,v"] + [f"{i},{i}" for i in range(500)]
    path.write_text("\n".join(lines), encoding="utf-8")
    data = read_spreadsheet_data(
        workspace, "uploads/many.csv", start_row=0, max_rows=10_000
    )
    assert data["row_count"] == MAX_READ_ROWS
    assert data["truncated"] is True


def test_write_output_xlsx_roundtrip(workspace: Path) -> None:
    out = workspace / "outputs" / "sales_summary.xlsx"
    summary = pd.DataFrame({"제품": ["A", "B"], "합계": [2800, 2500]})
    summary.to_excel(out, index=False)
    info = inspect_spreadsheet_data(workspace, "outputs/sales_summary.xlsx")
    assert info["sheets"][0]["rows"] == 2


def test_tools_bound_to_workspace(workspace: Path) -> None:
    path = workspace / "uploads" / "t.xlsx"
    _sample_xlsx(path)
    tools = {t.name: t for t in make_spreadsheet_tools(workspace)}
    assert "inspect_spreadsheet" in tools
    assert "read_spreadsheet" in tools
    raw = tools["inspect_spreadsheet"].invoke({"path": "uploads/t.xlsx"})
    assert "sales" in raw
    assert str(workspace) not in raw
