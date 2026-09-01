"""Normalized event vocabulary (tasking-agent style)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EventType = Literal[
    "status",
    "assistant_delta",
    "assistant_end",
    "tool_call",
    "tool_result",
    "file_view",
    "file_change",
    "interrupt",
    "test_result",
    "trace",
    "error",
    "done",
]


@dataclass
class AgentEvent:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)


def status(message: str, **extra: Any) -> AgentEvent:
    return AgentEvent("status", {"message": message, **extra})


def assistant_delta(text: str) -> AgentEvent:
    return AgentEvent("assistant_delta", {"text": text})


def assistant_end(text: str) -> AgentEvent:
    return AgentEvent("assistant_end", {"text": text})


def tool_call(name: str, arguments: dict[str, Any], call_id: str = "") -> AgentEvent:
    return AgentEvent(
        "tool_call",
        {"name": name, "arguments": arguments, "call_id": call_id},
    )


def tool_result(
    name: str,
    content: str,
    *,
    call_id: str = "",
    ok: bool = True,
    artifact: dict[str, Any] | None = None,
) -> AgentEvent:
    return AgentEvent(
        "tool_result",
        {
            "name": name,
            "content": content,
            "call_id": call_id,
            "ok": ok,
            "artifact": artifact or {},
        },
    )


def file_view(path: str, content: str, *, language: str = "", before: str | None = None) -> AgentEvent:
    data: dict[str, Any] = {"path": path, "content": content, "language": language}
    if before is not None:
        data["before"] = before
        data["action"] = "edit" if before != content else "view"
    return AgentEvent("file_view", data)


def file_change(path: str, diff: str, *, action: str = "modify") -> AgentEvent:
    return AgentEvent("file_change", {"path": path, "diff": diff, "action": action})


def interrupt(action_requests: list[dict[str, Any]], *, thread_id: str) -> AgentEvent:
    return AgentEvent(
        "interrupt",
        {"action_requests": action_requests, "thread_id": thread_id},
    )


def test_result(summary: str, *, ok: bool, details: str = "") -> AgentEvent:
    return AgentEvent(
        "test_result",
        {"summary": summary, "ok": ok, "details": details},
    )


def trace(step: str, detail: str = "", **extra: Any) -> AgentEvent:
    return AgentEvent("trace", {"step": step, "detail": detail, **extra})


def error(message: str) -> AgentEvent:
    return AgentEvent("error", {"message": message})


def done(*, interrupted: bool = False) -> AgentEvent:
    return AgentEvent("done", {"interrupted": interrupted})
