"""Timed permission grants that survive restarts.

A grant has two optional dimensions: a duration (or none, meaning until revoked) and a label saying what it is
for. What is durable is the expiry timestamp on disk, never a timer: on every startup ``reconcile`` revokes what
expired while the process was down and re-arms the rest with their true remaining time. A one-year grant works
the same way as a one-hour one. If the machine is off past an expiry, the grant ends late, on the next start,
never silently.

Two traps this handles:

- **Attribution.** A grant only revokes what it added. If the tool was already allowed for an unrelated reason,
  ending the grant must not strip it. Each record carries ``granted_by_us`` from the backend.
- **Long timers.** Platform waits take a fixed-width millisecond count (about 49.7 days on Windows), so a long
  timer raises OverflowError. Timers are capped at 30 days and chained.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from ..adapters.base import PermissionBackend

MAX_TIMER_SECONDS = 30 * 24 * 60 * 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def timer_plan(seconds: float) -> tuple[float, float]:
    """(seconds until the next wake-up, seconds still left after it). Chained when over the cap."""
    if seconds > MAX_TIMER_SECONDS:
        return MAX_TIMER_SECONDS, seconds - MAX_TIMER_SECONDS
    return max(seconds, 0.0), 0.0


class GrantStore:
    def __init__(self, path: str | Path, backend: PermissionBackend, clock: Callable[[], datetime] = _utcnow,
                 use_timers: bool = True):
        self.path = Path(path)
        self.backend = backend
        self.clock = clock
        self.use_timers = use_timers
        self._timers: dict[str, threading.Timer] = {}

    # -- state -----------------------------------------------------------------------------------------------------
    def _load(self) -> dict:
        if not self.path.exists():
            return {"grants": [], "log": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, state: dict) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8", newline="\n")
        tmp.replace(self.path)

    def _log(self, state: dict, tool: str, action: str, label: str | None) -> None:
        state["log"].append({"at": self.clock().isoformat(timespec="seconds"), "tool": tool, "action": action,
                             "label": label})

    def active(self) -> list[dict]:
        return self._load()["grants"]

    def history(self) -> list[dict]:
        return self._load()["log"]

    # -- operations ------------------------------------------------------------------------------------------------
    def grant(self, tool: str, label: str | None = None, minutes: int | None = None) -> dict:
        """minutes=None means indefinite: it lasts until revoke()."""
        added = self.backend.allow(tool)
        state = self._load()
        expires = (self.clock() + timedelta(minutes=minutes)).isoformat(timespec="seconds") if minutes else None
        prior = next((g for g in state["grants"] if g["tool"] == tool), None)
        record = {"tool": tool, "label": label, "granted_at": self.clock().isoformat(timespec="seconds"),
                  "expires_at": expires,
                  # a re-grant keeps the original attribution: if we added it the first time, we still own it
                  "granted_by_us": bool(added or (prior and prior.get("granted_by_us")))}
        state["grants"] = [g for g in state["grants"] if g["tool"] != tool] + [record]
        self._log(state, tool, "granted", label)
        self._save(state)
        if expires:
            self._arm(tool, minutes * 60)
        return record

    def revoke(self, tool: str, reason: str = "revoked (manual)") -> None:
        timer = self._timers.pop(tool, None)
        if timer:
            timer.cancel()
        state = self._load()
        grant = next((g for g in state["grants"] if g["tool"] == tool), None)
        action = reason
        if grant and grant.get("granted_by_us"):
            self.backend.revoke(tool)
        else:
            action += ", tracking only: the tool stays independently allowed"
        state["grants"] = [g for g in state["grants"] if g["tool"] != tool]
        self._log(state, tool, action, grant.get("label") if grant else None)
        self._save(state)

    def reconcile(self) -> list[tuple[str, float]]:
        """Run once at startup. Revokes grants that expired while the process was down; re-arms the rest. Returns
        (tool, seconds remaining) for every re-armed grant."""
        now = self.clock()
        armed = []
        for g in list(self.active()):
            if not g.get("expires_at"):
                continue                                   # indefinite: only revoke() ends it
            left = (datetime.fromisoformat(g["expires_at"]) - now).total_seconds()
            if left <= 0:
                self.revoke(g["tool"], "revoked (expired while offline)")
            else:
                self._arm(g["tool"], left)
                armed.append((g["tool"], left))
        return armed

    def _arm(self, tool: str, seconds: float) -> None:
        if not self.use_timers:
            return
        old = self._timers.pop(tool, None)
        if old:
            old.cancel()
        wait, rest = timer_plan(seconds)
        fn = (lambda: self._arm(tool, rest)) if rest else (lambda: self.revoke(tool, "revoked (expired)"))
        t = threading.Timer(wait, fn)
        t.daemon = True
        t.start()
        self._timers[tool] = t
