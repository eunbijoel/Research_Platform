"""User workbench: file editor, terminal, and preview (not deepagents-code agent shell)."""

from __future__ import annotations

import difflib
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from coding_agent.config import MAX_FILE_CHARS, SHELL_DENY

TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".toml",
    ".yml",
    ".yaml",
    ".html",
    ".htm",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".sh",
    ".sql",
    ".xml",
    ".csv",
    ".ini",
    ".env",
}
BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".woff",
    ".woff2",
    ".exe",
    ".dll",
    ".so",
    ".pyc",
}

USER_CMD_DENY = SHELL_DENY + (
    "rm -rf",
    "rm -fr",
    "sudo ",
    "su ",
    "chmod 777",
    "mkfs.",
    "curl | sh",
    "wget | sh",
    "> /etc/",
    "/dev/sd",
)


def resolve_workspace_file(workspace: Path, rel: str) -> Path:
    rel = (rel or "").strip().lstrip("/")
    if not rel or ".." in Path(rel).parts:
        raise PermissionError("Invalid path")
    root = workspace.resolve()
    target = (root / rel).resolve()
    target.relative_to(root)
    return target


def classify_file(path: Path) -> str:
    """Return 'text', 'binary', or 'missing'."""
    if not path.is_file():
        return "missing"
    if path.suffix.lower() in BINARY_SUFFIXES:
        return "binary"
    if path.suffix.lower() in TEXT_SUFFIXES:
        return "text"
    try:
        sample = path.read_bytes()[:4096]
    except OSError:
        return "binary"
    if b"\x00" in sample:
        return "binary"
    return "text"


def read_workspace_text(workspace: Path, rel: str) -> tuple[str | None, str | None]:
    try:
        path = resolve_workspace_file(workspace, rel)
    except PermissionError as exc:
        return None, str(exc)
    kind = classify_file(path)
    if kind == "missing":
        return None, "File not found"
    if kind == "binary":
        return None, "Binary file — open in an external tool"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, f"Read failed: {exc}"
    if len(text) > MAX_FILE_CHARS:
        text = text[:MAX_FILE_CHARS] + "\n… (truncated)"
    return text, None


def write_workspace_text(workspace: Path, rel: str, content: str) -> tuple[bool, str | None]:
    try:
        path = resolve_workspace_file(workspace, rel)
    except PermissionError as exc:
        return False, str(exc)
    if classify_file(path) == "binary":
        return False, "Refusing to overwrite a binary file"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return False, f"Write failed: {exc}"
    return True, None


def unified_diff(old: str, new: str, path: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )


def validate_user_command(command: str) -> tuple[bool, str]:
    cmd = (command or "").strip()
    if not cmd:
        return False, "Enter a command"
    low = cmd.lower()
    for bad in USER_CMD_DENY:
        if bad.lower() in low:
            return False, f"Blocked pattern: {bad!r}"
    if re.search(r"\bcd\b", low) and re.search(r"\.\.", cmd):
        return False, "cd outside workspace is not allowed"
    return True, ""


def pick_free_port(start: int = 8765, end: int = 8820) -> int | None:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    return None


def detect_preview_kind(path: Path, text: str) -> str:
    if path.suffix.lower() in {".html", ".htm"}:
        return "html"
    low = text.lower()
    if "import streamlit" in low or "from streamlit" in low:
        return "streamlit"
    if "fastapi" in low or "uvicorn" in low:
        return "fastapi"
    if "flask" in low or "from flask" in low:
        return "flask"
    return "python"


def preview_command(kind: str, target: Path, port: int) -> list[str]:
    if kind == "streamlit":
        return [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(target),
            "--server.headless",
            "true",
            "--server.port",
            str(port),
            "--browser.gatherUsageStats",
            "false",
        ]
    if kind == "fastapi":
        # uvicorn module:app — user file often defines `app`
        return [
            sys.executable,
            "-m",
            "uvicorn",
            f"{target.stem}:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]
    return [sys.executable, str(target)]


def start_preview_process(
    workspace: Path,
    rel: str,
    *,
    port: int | None = None,
) -> dict[str, Any]:
    path = resolve_workspace_file(workspace, rel)
    text, err = read_workspace_text(workspace, rel)
    if err:
        return {"ok": False, "error": err}
    assert text is not None
    kind = detect_preview_kind(path, text)
    if kind == "html":
        return {"ok": True, "kind": "html", "target": rel, "port": None, "pid": None}
    chosen = port or pick_free_port()
    if chosen is None:
        return {"ok": False, "error": "No free port in range 8765–8819"}
    cmd = preview_command(kind, path, chosen)
    env = {**dict(**__import__("os").environ), "PORT": str(chosen)}
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(workspace.resolve()),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "kind": kind,
        "target": rel,
        "port": chosen,
        "pid": proc.pid,
        "proc": proc,
        "started_at": time.time(),
        "log": [f"$ {' '.join(cmd)}\n"],
    }


def poll_preview(state: dict[str, Any]) -> dict[str, Any]:
    proc = state.get("proc")
    if proc is None:
        state["running"] = False
        return state
    if proc.poll() is not None:
        rest = proc.stdout.read() if proc.stdout else ""
        if rest:
            state.setdefault("log", []).append(rest)
        state["exit_code"] = proc.returncode
        state["proc"] = None
        state["running"] = False
        return state
    if proc.stdout:
        try:
            import select

            if select.select([proc.stdout], [], [], 0)[0]:
                chunk = proc.stdout.read(4096)
                if chunk:
                    state.setdefault("log", []).append(chunk)
        except Exception:  # noqa: BLE001
            pass
    state["running"] = True
    return state


def stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def start_user_command_bg(workspace: Path, command: str) -> dict[str, Any]:
    ok, msg = validate_user_command(command)
    if not ok:
        return {"ok": False, "error": msg}
    try:
        proc = subprocess.Popen(
            ["bash", "-lc", command],
            cwd=str(workspace.resolve()),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=os_setsid,
        )
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "proc": proc, "command": command, "started_at": time.time()}


def os_setsid() -> None:
    import os

    os.setsid()


def poll_user_terminal(proc: subprocess.Popen | None) -> tuple[str, bool, int | None]:
    if proc is None:
        return "", False, None
    code = proc.poll()
    chunks: list[str] = []
    if proc.stdout:
        try:
            import select

            while select.select([proc.stdout], [], [], 0)[0]:
                part = proc.stdout.read(4096)
                if not part:
                    break
                chunks.append(part)
        except Exception:  # noqa: BLE001
            if code is not None and proc.stdout:
                chunks.append(proc.stdout.read() or "")
    running = code is None
    return "".join(chunks), running, code


def stop_user_terminal(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    if proc.poll() is not None:
        return
    try:
        import os

        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:  # noqa: BLE001
        stop_process(proc)
