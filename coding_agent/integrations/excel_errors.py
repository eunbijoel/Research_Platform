"""Transport and Excel status codes for the Excel custom tool."""

from __future__ import annotations

TRANSPORT_CONFIGURATION_ERROR = "configuration_error"
TRANSPORT_INVALID_TOOL_INPUT = "invalid_tool_input"
TRANSPORT_WORKSPACE_VIOLATION = "workspace_violation"
TRANSPORT_EXCEL_CLI_UNAVAILABLE = "excel_cli_unavailable"
TRANSPORT_SUBPROCESS_TIMEOUT = "subprocess_timeout"
TRANSPORT_SUBPROCESS_FAILED = "subprocess_failed"
TRANSPORT_MALFORMED_RESPONSE = "malformed_response"
TRANSPORT_PROTOCOL_MISMATCH = "protocol_mismatch"
TRANSPORT_ARTIFACT_VALIDATION_FAILED = "artifact_validation_failed"
TRANSPORT_EXCEL_RESPONSE = "excel_response"
TRANSPORT_SOURCE_MUTATED = "source_mutated"

EXCEL_CONTRACT_VERSION = "1.0"
EXCEL_STATUSES = frozenset(
    {
        "success",
        "invalid_request",
        "cannot_plan",
        "validation_failed",
        "model_unavailable",
        "timeout",
        "cancelled",
        "execution_failed",
    }
)

ALLOWED_INPUT_EXTENSIONS = frozenset({".xlsx", ".xls", ".xlsm", ".csv"})
TRANSFORM_ALLOWED_EXTENSIONS = frozenset({".xlsx"})
ALLOWED_ANALYSIS_MODES = frozenset({"single", "multi"})
ALLOWED_ARTIFACT_KINDS = frozenset({"table", "workbook", "chart"})
ALLOWED_MEDIA_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}

DIAGNOSTIC_LIMIT = 500
TEXT_LIMIT = 4000
PREVIEW_ROW_LIMIT = 10
PREVIEW_COLUMN_LIMIT = 50
CELL_CHAR_LIMIT = 200
GRACE_PERIOD_SECONDS = 1.0
