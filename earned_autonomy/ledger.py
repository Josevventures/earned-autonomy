"""Append-only JSONL decision ledger.

One JSON object per line, never rewritten. Every record has an ``id`` (the idempotency key: appending an id that is
already present changes nothing), a ``kind`` and an ``at`` timestamp. Kinds:

- ``ask``: the agent put a question to the user through an unattended channel (a queue, a chat, a message).
- ``decision``: the user's call on a proposed action: ``approved``, ``rejected`` or ``modified``.
- ``outcome``: what the user did after an autonomous action: ``kept`` or ``overridden``.
- ``graduation_proposed``: the agent proposed that a type run autonomously.
- ``graduation_answer``: the user's answer to that proposal. Not a decision about an action, so never tallied.

The ledger knows nothing about tiers; ``policy.evaluate`` replays it.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .policy import DECISION_CALLS, OUTCOME_RESULTS

KINDS = {
    "ask": ("action_type", "proposed"),
    "decision": ("action_type", "proposed", "call"),
    "outcome": ("action_type", "result"),
    "graduation_proposed": ("action_type",),
    "graduation_answer": ("action_type", "proposal", "call"),
}


class LedgerError(ValueError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate(rec: dict) -> None:
    kind = rec.get("kind")
    if kind not in KINDS:
        raise LedgerError(f"unknown record kind {kind!r}; expected one of {sorted(KINDS)}")
    for key in ("id", "at", *KINDS[kind]):
        if not str(rec.get(key, "")).strip():
            raise LedgerError(f"{kind} record needs a non-empty {key!r}")
    if kind == "decision" and rec["call"] not in DECISION_CALLS:
        raise LedgerError(f"decision call must be one of {sorted(DECISION_CALLS)}, got {rec['call']!r}")
    if kind == "outcome" and rec["result"] not in OUTCOME_RESULTS:
        raise LedgerError(f"outcome result must be one of {sorted(OUTCOME_RESULTS)}, got {rec['result']!r}")
    if kind == "graduation_answer" and rec["call"] not in ("approved", "rejected"):
        raise LedgerError(f"graduation answer must be approved or rejected, got {rec['call']!r}")


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def records(self) -> list[dict]:
        """Every record, in order. A malformed line fails loudly, except a torn final line with no trailing newline
        (a crash mid-append), which is skipped: it was never a complete record."""
        if not self.path.exists():
            return []
        raw = self.path.read_text(encoding="utf-8")
        lines = raw.split("\n")
        out = []
        for i, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                if i == len(lines) and not raw.endswith("\n"):
                    continue
                raise LedgerError(f"{self.path.name} line {i} is not valid JSON: {e}") from e
            validate(rec)
            out.append(rec)
        return out

    def find(self, record_id: str) -> dict | None:
        return next((r for r in self.records() if r["id"] == record_id), None)

    def append(self, rec: dict) -> bool:
        """Append one record. Returns False, writing nothing, if a record with that id already exists."""
        rec = {**rec}
        rec.setdefault("at", now_iso())
        validate(rec)
        if self.find(rec["id"]) is not None:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        needs_newline = self.path.exists() and self.path.stat().st_size > 0 and \
            not self.path.read_bytes().endswith(b"\n")
        with open(self.path, "a", encoding="utf-8", newline="\n") as f:
            if needs_newline:
                f.write("\n")                          # seal a torn last line so this record stays parseable
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return True
