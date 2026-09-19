"""Safe Excel/CSV inspect & read for the workbench and Deep Agent tools."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

UPLOAD_DIR = "uploads"
# Legacy chat-upload dir; wiped on new chat if leftovers remain.
SESSION_UPLOAD_DIR = ".session_uploads"
OUTPUT_DIR = "outputs"
ALLOWED_SUFFIXES = {".xlsx", ".xls", ".csv"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MiB
DEFAULT_PREVIEW_ROWS = 20
DEFAULT_READ_ROWS = 50
MAX_READ_ROWS = 200
INSPECT_SAMPLE_ROWS = 5
CSV_ENCODINGS = ("utf-8", "utf-8-sig", "cp949", "euc-kr", "latin-1")


def ensure_upload_dirs(workspace: Path) -> tuple[Path, Path]:
    uploads = workspace / UPLOAD_DIR
    outputs = workspace / OUTPUT_DIR
    uploads.mkdir(parents=True, exist_ok=True)
    outputs.mkdir(parents=True, exist_ok=True)
    return uploads, outputs


def clear_session_uploads(workspace: Path) -> None:
    """Remove leftover files under ``.session_uploads/`` (legacy ephemeral path)."""
    path = workspace / SESSION_UPLOAD_DIR
    if not path.is_dir():
        return
    for child in path.iterdir():
        if child.is_file():
            try:
                child.unlink()
            except OSError:
                pass


def sanitize_filename(name: str) -> str:
    """Strip path segments and dangerous characters; block traversal."""
    raw = (name or "").strip()
    if not raw:
        raise ValueError("Invalid filename")
    # Reject absolute / home paths; relative folders are reduced to basename.
    if Path(raw).is_absolute() or raw.startswith(("/", "\\", "~")):
        raise ValueError("Invalid filename")
    base = Path(raw).name
    base = base.replace("\x00", "")
    base = re.sub(r"[\\/]+", "", base)
    if not base or base in {".", ".."} or ".." in base:
        raise ValueError("Invalid filename")
    if Path(base).suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError(
            f"Unsupported file type. Allowed: {', '.join(sorted(ALLOWED_SUFFIXES))}"
        )
    return base


def unique_dest(directory: Path, filename: str) -> Path:
    target = directory / filename
    if not target.exists():
        return target
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    n = 1
    while True:
        candidate = directory / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def resolve_under_workspace(workspace: Path, rel: str) -> Path:
    """Resolve a workspace-relative path; raise if outside workspace."""
    raw = (rel or "").strip()
    if not raw:
        raise PermissionError("Path must stay inside the workspace")
    # Absolute paths (POSIX or Windows) must never be accepted.
    candidate = Path(raw)
    if candidate.is_absolute() or raw.startswith(("/", "\\", "~")):
        raise PermissionError("Path must stay inside the workspace")
    rel = raw.lstrip("/")
    if not rel or ".." in Path(rel).parts:
        raise PermissionError("Path must stay inside the workspace")
    root = workspace.resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PermissionError("Path must stay inside the workspace") from exc
    return target


def is_spreadsheet_path(path: Path | str) -> bool:
    return Path(path).suffix.lower() in ALLOWED_SUFFIXES


def save_upload(
    workspace: Path,
    *,
    filename: str,
    data: bytes,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Save upload bytes under workspace/uploads/. Returns relative path info.

    Chat attachments use this path so files remain visible in Files across chats.
    """
    if len(data) > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES / (1024 * 1024)
        raise ValueError(f"File too large (max {mb:.0f} MB)")
    safe_name = sanitize_filename(filename)
    uploads, _ = ensure_upload_dirs(workspace)
    if overwrite:
        dest = uploads / safe_name
    else:
        dest = unique_dest(uploads, safe_name)
    dest.write_bytes(data)
    rel = str(dest.relative_to(workspace.resolve()))
    return {
        "ok": True,
        "path": rel,
        "name": dest.name,
        "bytes": len(data),
        "overwritten": overwrite and (uploads / safe_name) == dest,
    }


def _import_pandas():
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError(
            "pandas is required for spreadsheet support. "
            "Install with: uv pip install pandas openpyxl xlrd"
        ) from exc
    return pd


def _read_csv_dataframe(path: Path, *, nrows: int | None = None):
    pd = _import_pandas()
    last_err: Exception | None = None
    for enc in CSV_ENCODINGS:
        try:
            return pd.read_csv(path, encoding=enc, nrows=nrows)
        except UnicodeDecodeError as exc:
            last_err = exc
            continue
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            break
    raise ValueError(f"Failed to read CSV: {last_err}")


def _excel_sheet_names(path: Path) -> list[str]:
    pd = _import_pandas()
    engine = "xlrd" if path.suffix.lower() == ".xls" else "openpyxl"
    xl = pd.ExcelFile(path, engine=engine)
    try:
        return list(xl.sheet_names)
    finally:
        xl.close()


def _read_excel_dataframe(
    path: Path,
    *,
    sheet: str | int | None = 0,
    nrows: int | None = None,
    usecols: list[str] | None = None,
):
    pd = _import_pandas()
    engine = "xlrd" if path.suffix.lower() == ".xls" else "openpyxl"
    kwargs: dict[str, Any] = {"engine": engine, "sheet_name": sheet if sheet is not None else 0}
    if nrows is not None:
        kwargs["nrows"] = nrows
    if usecols:
        kwargs["usecols"] = usecols
    return pd.read_excel(path, **kwargs)


def _dtype_map(df) -> dict[str, str]:
    return {str(c): str(t) for c, t in df.dtypes.items()}


def _sample_records(df, n: int) -> list[dict[str, Any]]:
    if df.empty:
        return []
    sample = df.head(n).copy()
    # Make JSON-serializable (dates → str, NaN → None)
    for col in sample.columns:
        if str(sample[col].dtype).startswith("datetime"):
            sample[col] = sample[col].astype(str)
    records = sample.where(sample.notna(), None).to_dict(orient="records")
    # stringify leftover non-JSON types
    cleaned: list[dict[str, Any]] = []
    for row in records:
        cleaned.append({str(k): _jsonable(v) for k, v in row.items()})
    return cleaned


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def inspect_spreadsheet_data(workspace: Path, rel: str) -> dict[str, Any]:
    path = resolve_under_workspace(workspace, rel)
    if not path.is_file():
        raise FileNotFoundError("File not found")
    if not is_spreadsheet_path(path):
        raise ValueError("Not a supported spreadsheet (.xlsx, .xls, .csv)")

    suffix = path.suffix.lower()
    result: dict[str, Any] = {
        "file": Path(rel).name,
        "path": str(path.relative_to(workspace.resolve())),
        "format": suffix.lstrip("."),
        "sheets": [],
    }

    if suffix == ".csv":
        df = _read_csv_dataframe(path)
        result["sheets"] = [
            {
                "name": "csv",
                "rows": int(len(df)),
                "columns": int(df.shape[1]),
                "column_names": [str(c) for c in df.columns],
                "dtypes": _dtype_map(df),
                "sample_rows": _sample_records(df, INSPECT_SAMPLE_ROWS),
            }
        ]
        return result

    names = _excel_sheet_names(path)
    sheets: list[dict[str, Any]] = []
    for name in names:
        df = _read_excel_dataframe(path, sheet=name)
        sheets.append(
            {
                "name": name,
                "rows": int(len(df)),
                "columns": int(df.shape[1]),
                "column_names": [str(c) for c in df.columns],
                "dtypes": _dtype_map(df),
                "sample_rows": _sample_records(df, INSPECT_SAMPLE_ROWS),
            }
        )
    result["sheets"] = sheets
    return result


def read_spreadsheet_data(
    workspace: Path,
    rel: str,
    *,
    sheet: str | None = None,
    columns: list[str] | None = None,
    start_row: int = 0,
    max_rows: int = DEFAULT_READ_ROWS,
) -> dict[str, Any]:
    path = resolve_under_workspace(workspace, rel)
    if not path.is_file():
        raise FileNotFoundError("File not found")
    if not is_spreadsheet_path(path):
        raise ValueError("Not a supported spreadsheet (.xlsx, .xls, .csv)")

    start_row = max(0, int(start_row))
    max_rows = max(1, min(int(max_rows), MAX_READ_ROWS))
    end = start_row + max_rows
    suffix = path.suffix.lower()

    if suffix == ".csv":
        df = _read_csv_dataframe(path)
        sheet_name = "csv"
    else:
        sheet_name = sheet
        if sheet_name is None:
            names = _excel_sheet_names(path)
            if not names:
                raise ValueError("Workbook has no sheets")
            sheet_name = names[0]
        df = _read_excel_dataframe(path, sheet=sheet_name)

    if columns:
        missing = [c for c in columns if c not in df.columns]
        if missing:
            raise ValueError(f"Unknown columns: {', '.join(missing)}")
        df = df[columns]

    total = int(len(df))
    sliced = df.iloc[start_row:end]
    return {
        "file": Path(rel).name,
        "path": str(path.relative_to(workspace.resolve())),
        "sheet": sheet_name,
        "start_row": start_row,
        "row_count": int(len(sliced)),
        "total_rows": total,
        "columns": [str(c) for c in sliced.columns],
        "dtypes": _dtype_map(sliced),
        "rows": _sample_records(sliced, len(sliced)),
        "truncated": end < total,
        "max_rows_cap": MAX_READ_ROWS,
    }


def preview_spreadsheet(
    workspace: Path,
    rel: str,
    *,
    sheet: str | None = None,
    preview_rows: int = DEFAULT_PREVIEW_ROWS,
) -> dict[str, Any]:
    """UI-oriented preview payload (never raises absolute paths)."""
    try:
        path = resolve_under_workspace(workspace, rel)
        if not path.is_file():
            return {"ok": False, "error": "File not found"}
        if not is_spreadsheet_path(path):
            return {"ok": False, "error": "Not a spreadsheet file"}

        suffix = path.suffix.lower()
        if suffix == ".csv":
            df = _read_csv_dataframe(path)
            return {
                "ok": True,
                "format": "csv",
                "sheets": ["csv"],
                "sheet": "csv",
                "rows": int(len(df)),
                "columns": int(df.shape[1]),
                "column_names": [str(c) for c in df.columns],
                "dtypes": _dtype_map(df),
                "preview": df.head(preview_rows),
            }

        names = _excel_sheet_names(path)
        active = sheet if sheet in names else (names[0] if names else None)
        if active is None:
            return {"ok": False, "error": "Workbook has no sheets"}
        df = _read_excel_dataframe(path, sheet=active)
        return {
            "ok": True,
            "format": suffix.lstrip("."),
            "sheets": names,
            "sheet": active,
            "rows": int(len(df)),
            "columns": int(df.shape[1]),
            "column_names": [str(c) for c in df.columns],
            "dtypes": _dtype_map(df),
            "preview": df.head(preview_rows),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def format_upload_context(paths: list[str]) -> str:
    if not paths:
        return ""
    lines = [
        "[Spreadsheet attachments — workspace-relative paths under uploads/]",
        "These files are kept in uploads/ (visible in Files) across chats.",
        "Prefer inspect_spreadsheet for workbook structure (sheets, columns, metadata).",
        "Prefer read_spreadsheet for a bounded row/column slice.",
        "Use analyze_excel only for natural-language summary, comparison, or aggregation.",
        "Use transform_excel for extract_to_sheet / unmerge_cells on a copy (never mutate originals).",
        "Excel results are saved under outputs/excel_agent/ and show up in Files for download.",
        "Do not run Excel Analyzer via the execute shell; do not reimplement that analysis in pandas.",
        "Do not use read_file on .xlsx/.xls (binary).",
        "If multiple files exist and the user is unclear which one, ask for the filename.",
        "",
    ]
    for p in paths:
        lines.append(f"- {p}")
    return "\n".join(lines)


def make_spreadsheet_tools(workspace: Path):
    """Build LangChain tools bound to a workspace root."""

    @tool
    def inspect_spreadsheet(path: str) -> str:
        """Inspect workbook structure: sheet names, columns, and bounded metadata.

        Args:
            path: Workspace-relative path (e.g. uploads/sales.xlsx).
        """
        try:
            data = inspect_spreadsheet_data(workspace, path)
            return json.dumps(data, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    @tool
    def read_spreadsheet(
        path: str,
        sheet: str | None = None,
        columns: list[str] | None = None,
        start_row: int = 0,
        max_rows: int = DEFAULT_READ_ROWS,
    ) -> str:
        """Read a bounded row/column slice from an Excel/CSV file under the workspace.

        Args:
            path: Workspace-relative path (e.g. uploads/sales.xlsx).
            sheet: Sheet name for Excel (ignored for CSV). Defaults to first sheet.
            columns: Optional list of column names to include.
            start_row: 0-based start row.
            max_rows: Number of rows to return (capped).
        """
        try:
            data = read_spreadsheet_data(
                workspace,
                path,
                sheet=sheet,
                columns=columns,
                start_row=start_row,
                max_rows=max_rows,
            )
            return json.dumps(data, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    return [inspect_spreadsheet, read_spreadsheet]
