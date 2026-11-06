"""Neutral interfaces the core depends on.

The core never sends a message, calls a mail or calendar API, or touches a dashboard. An integration supplies:

- a ``PermissionBackend``, which adds and removes allow rules wherever the agent runtime keeps them;
- an approval channel of its own choosing. The contract is two calls into ``decisions``: ``ask`` when a question
  goes out, and ``log_decision(..., via_queue=True)`` when its answer comes back. The guard refuses any answer to
  a question that was never asked, so a transcriber cannot originate a decision.
"""
from __future__ import annotations

from typing import Protocol


class PermissionBackend(Protocol):
    def allow(self, tool: str) -> bool:
        """Add an allow rule. Return True only if this call added it (False if it was already allowed)."""

    def revoke(self, tool: str) -> None:
        """Remove an allow rule this backend added."""


class InMemoryBackend:
    def __init__(self, allowed: set[str] | None = None):
        self.allowed = set(allowed or ())

    def allow(self, tool: str) -> bool:
        if tool in self.allowed:
            return False
        self.allowed.add(tool)
        return True

    def revoke(self, tool: str) -> None:
        self.allowed.discard(tool)
