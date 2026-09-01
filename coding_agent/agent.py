"""Thin facade — prefer DeepAgentsBridge; kept for import compatibility."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from coding_agent.bridge import DeepAgentsBridge
from coding_agent.events import AgentEvent


def run_agent(
    user_prompt: str,
    workspace: Path,
    *,
    history: list[dict[str, Any]] | None = None,
    model: str | None = None,
    thread_id: str = "default",
    auto_approve: bool = False,
) -> Iterator[AgentEvent]:
    """Run one turn through deepagents-code via DeepAgentsBridge.

    `history` is ignored when using the LangGraph checkpointer (thread_id).
    """
    del history  # checkpointed by thread_id
    bridge = DeepAgentsBridge(workspace, model=model, auto_approve=auto_approve)
    yield from bridge.run(user_prompt, thread_id=thread_id)
