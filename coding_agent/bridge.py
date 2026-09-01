"""DeepAgentsBridge — Streamlit UI ↔ deepagents-code runtime.

Inspired by tasking-agent's AgentBridge / DeepAgentsBridge pattern:
the UI never talks to Ollama tool loops directly; all agent execution goes
through `deepagents_code.agent.create_cli_agent`.
"""

from __future__ import annotations

import difflib
import py_compile
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from coding_agent import events as ev
from coding_agent.config import (
    DATA_DIR,
    MAX_FILE_CHARS,
    MAX_TOOL_ROUNDS,
    MODEL_NAME,
    resolve_workspace,
)
from coding_agent.events import AgentEvent

# Prove at import time that deepagents-code is the runtime dependency.
import deepagents_code as _deepagents_code  # noqa: F401
from deepagents_code.agent import create_cli_agent

USER_HINT = (
    "[Workbench] Prefer tools over guessing; reply in the user's language; "
    "after edits briefly explain what changed. When done, leave code in a "
    "runnable state.\n\n"
)

def normalize_model(model: str | None) -> str:
    name = (model or MODEL_NAME).strip() or MODEL_NAME
    if ":" in name and name.split(":", 1)[0] in {
        "ollama",
        "openai",
        "anthropic",
        "google_genai",
        "bedrock",
        "groq",
        "fireworks",
    }:
        return name
    return f"ollama:{name}"


def deepagents_version() -> str:
    return getattr(_deepagents_code, "__version__", "unknown")


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content)


def snapshot_workspace(workspace: Path) -> dict[str, str]:
    """Relative path → text content for text-ish files."""
    out: dict[str, str] = {}
    root = workspace.resolve()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(p.startswith(".") for p in path.relative_to(root).parts):
            continue
        if path.suffix.lower() not in {
            ".py",
            ".md",
            ".txt",
            ".json",
            ".toml",
            ".yml",
            ".yaml",
            ".html",
            ".css",
            ".js",
            ".ts",
            ".tsx",
            ".jsx",
            ".sh",
        }:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS]
        out[str(path.relative_to(root))] = text
    return out


def diff_snapshots(before: dict[str, str], after: dict[str, str]) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    for path in sorted(set(before) | set(after)):
        old = before.get(path)
        new = after.get(path)
        if old == new:
            continue
        if old is None:
            action = "create"
            diff = "\n".join(f"+ {line}" for line in (new or "").splitlines()[:80])
        elif new is None:
            action = "delete"
            diff = "\n".join(f"- {line}" for line in old.splitlines()[:80])
        else:
            action = "modify"
            diff = "\n".join(
                difflib.unified_diff(
                    old.splitlines(),
                    new.splitlines(),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                    lineterm="",
                )
            )
        changes.append({"path": path, "action": action, "diff": diff[:8000]})
    return changes


def run_verification(workspace: Path) -> tuple[bool, str, str]:
    """Compile Python files; run pytest when present. Returns (ok, summary, details)."""
    root = workspace.resolve()
    details: list[str] = []
    ok = True

    py_files = [
        p
        for p in root.rglob("*.py")
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(root).parts)
    ]
    compile_fail = 0
    for path in py_files:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            compile_fail += 1
            ok = False
            details.append(f"py_compile FAIL {path.relative_to(root)}: {exc}")
    details.append(f"py_compile: {len(py_files) - compile_fail}/{len(py_files)} ok")

    has_tests = (root / "tests").is_dir() or (root / "test").is_dir() or any(
        root.glob("test_*.py")
    )
    if has_tests:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--tb=line"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=120,
            )
            out = ((proc.stdout or "") + (proc.stderr or "")).strip()
            details.append(out[:4000] or "(pytest no output)")
            if proc.returncode != 0:
                ok = False
                details.append(f"pytest exit={proc.returncode}")
            else:
                details.append("pytest: passed")
        except Exception as exc:  # noqa: BLE001
            details.append(f"pytest skipped/failed to run: {exc}")

    summary = "verification ok" if ok else "verification failed"
    return ok, summary, "\n".join(details)


class DeepAgentsBridge:
    """Owns the deepagents-code graph, SQLite checkpointer, and UI event mapping."""

    def __init__(
        self,
        workspace: Path | str | None = None,
        *,
        model: str | None = None,
        data_dir: Path | str | None = None,
        auto_approve: bool = False,
    ) -> None:
        self.workspace = resolve_workspace(workspace)
        self.model = normalize_model(model)
        self.data_dir = Path(data_dir or DATA_DIR)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.auto_approve = auto_approve

        self._conn = sqlite3.connect(
            str(self.data_dir / "checkpoints.sqlite"),
            check_same_thread=False,
        )
        self._checkpointer = SqliteSaver(self._conn)
        self._agent = None

    @property
    def agent(self):
        if self._agent is None:
            self._agent, _ = create_cli_agent(
                model=self.model,
                assistant_id="coding-agent-ui",
                cwd=self.workspace,
                interactive=False,
                auto_approve=self.auto_approve,
                enable_ask_user=False,
                enable_memory=False,
                enable_skills=False,
                enable_shell=True,
                checkpointer=self._checkpointer,
            )
        return self._agent

    def reset_agent(self) -> None:
        """Force recreate (e.g. after model / auto_approve change)."""
        self._agent = None

    def _config(self, thread_id: str) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": max(25, MAX_TOOL_ROUNDS * 8),
        }

    def _pending_interrupt(self, thread_id: str) -> list[dict[str, Any]] | None:
        state = self.agent.get_state(self._config(thread_id))
        if not state.next:
            return None
        requests: list[dict[str, Any]] = []
        for task in state.tasks or ():
            for intr in getattr(task, "interrupts", ()) or ():
                value = getattr(intr, "value", intr)
                if isinstance(value, dict) and value.get("action_requests"):
                    for ar in value["action_requests"]:
                        requests.append(
                            {
                                "name": ar.get("name"),
                                "args": ar.get("args") or {},
                                "description": ar.get("description") or "",
                            }
                        )
                elif isinstance(value, dict):
                    requests.append(value)
        return requests or None

    def _map_stream(
        self,
        stream_input: Any,
        *,
        thread_id: str,
        before_snap: dict[str, str] | None = None,
    ) -> Iterator[AgentEvent]:
        pending_args: dict[str, tuple[str, dict[str, Any]]] = {}
        final_text = ""
        config = self._config(thread_id)

        try:
            for chunk in self.agent.stream(
                stream_input,
                stream_mode="updates",
                config=config,
            ):
                if not isinstance(chunk, dict):
                    continue
                for node, update in chunk.items():
                    if update is None or not isinstance(update, dict):
                        continue

                    if node == "model":
                        for m in update.get("messages") or []:
                            if not isinstance(m, AIMessage):
                                continue
                            tool_calls = list(m.tool_calls or [])
                            text = _content_text(m.content).strip()
                            for tc in tool_calls:
                                name = tc.get("name") or ""
                                args = tc.get("args") or {}
                                call_id = str(tc.get("id") or "")
                                pending_args[call_id] = (
                                    name,
                                    args if isinstance(args, dict) else {},
                                )
                                yield ev.tool_call(
                                    name,
                                    args if isinstance(args, dict) else {"_raw": args},
                                    call_id=call_id,
                                )
                                yield ev.trace("tool_call", name, arguments=args)
                            if text and not tool_calls:
                                final_text = text
                                yield ev.assistant_delta(text)
                                yield ev.assistant_end(text)
                            elif text and tool_calls:
                                yield ev.assistant_delta(text)

                    elif node == "tools":
                        for m in update.get("messages") or []:
                            if not isinstance(m, ToolMessage):
                                continue
                            name = getattr(m, "name", "") or ""
                            call_id = str(getattr(m, "tool_call_id", "") or "")
                            content = _content_text(m.content)
                            status = getattr(m, "status", None)
                            ok = status != "error"
                            yield ev.tool_result(name, content, call_id=call_id, ok=ok)
                            yield ev.trace("tool_result", name, ok=ok)
                            args: dict[str, Any] = {}
                            if call_id in pending_args:
                                name2, args = pending_args.pop(call_id)
                                name = name or name2
                            fv = self._maybe_file_view(name, args, content)
                            if fv:
                                yield fv

                    elif node.endswith("before_model") or node.endswith("after_model"):
                        yield ev.trace("middleware", node)

        except Exception as exc:  # noqa: BLE001
            yield ev.error(f"deepagents-code run failed: {exc}")
            yield ev.done(interrupted=False)
            return

        action_requests = self._pending_interrupt(thread_id)
        if action_requests:
            yield ev.interrupt(action_requests, thread_id=thread_id)
            yield ev.status("Waiting for approval…")
            yield ev.done(interrupted=True)
            return

        if before_snap is not None:
            after = snapshot_workspace(self.workspace)
            for change in diff_snapshots(before_snap, after):
                yield ev.file_change(
                    change["path"],
                    change["diff"],
                    action=change["action"],
                )
                # Also refresh code pane
                path = change["path"]
                after_text = after.get(path, "")
                before_text = before_snap.get(path)
                yield ev.file_view(
                    path,
                    after_text,
                    language=Path(path).suffix.lstrip("."),
                    before=before_text,
                )

            vok, summary, details = run_verification(self.workspace)
            yield ev.test_result(summary, ok=vok, details=details)
            yield ev.trace("verify", summary, ok=vok)

        if final_text:
            yield ev.trace("complete", "assistant reply ready")
        yield ev.done(interrupted=False)

    def _maybe_file_view(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: str,
    ) -> AgentEvent | None:
        path = args.get("path") or args.get("file_path") or args.get("file")
        if not path or not isinstance(path, str):
            return None
        p = Path(path)
        try:
            if p.is_absolute():
                target = p.resolve()
                rel = target.relative_to(self.workspace.resolve())
            else:
                target = (self.workspace / path).resolve()
                rel = target.relative_to(self.workspace.resolve())
        except Exception:  # noqa: BLE001
            return None
        name = (tool_name or "").lower()
        if name in {"read_file", "write_file", "edit_file"} and target.is_file():
            try:
                text = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = result
            if len(text) > MAX_FILE_CHARS:
                text = text[:MAX_FILE_CHARS] + "\n… (truncated)"
            return ev.file_view(str(rel), text, language=target.suffix.lstrip("."))
        return None

    def run(
        self,
        user_prompt: str,
        *,
        thread_id: str,
    ) -> Iterator[AgentEvent]:
        """Start or continue a thread turn via deepagents-code."""
        yield ev.status(
            f"model={self.model} · deepagents-code {deepagents_version()}",
            workspace=str(self.workspace),
            thread_id=thread_id,
            auto_approve=self.auto_approve,
        )
        yield ev.trace("start", f"prompt length={len(user_prompt)}")
        before = snapshot_workspace(self.workspace)
        yield ev.status("running deepagents-code…")
        yield from self._map_stream(
            {"messages": [HumanMessage(content=USER_HINT + user_prompt)]},
            thread_id=thread_id,
            before_snap=before,
        )

    def resume(
        self,
        *,
        thread_id: str,
        decisions: list[dict[str, Any]],
    ) -> Iterator[AgentEvent]:
        """Resume after HITL approve/reject."""
        before = snapshot_workspace(self.workspace)
        yield ev.status("resuming after approval…")
        yield from self._map_stream(
            Command(resume={"decisions": decisions}),
            thread_id=thread_id,
            before_snap=before,
        )
