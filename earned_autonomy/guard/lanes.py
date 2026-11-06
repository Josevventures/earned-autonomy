"""Permission lanes for dispatched agent jobs.

A lane is the set of tools a headless job may use. The lesson this module encodes: **a deny list is not a
boundary.** If the job inherits whatever the ambient settings allow, every tool the deny list forgets to name is
usable. The lane that cost a real file write named ``Bash`` but not ``PowerShell``; the ambient settings allowed
``PowerShell``, so a "read-only" job wrote a file with it. The fix is an allowlist boundary: the job launches
with only the lane's tools, ignores ambient settings, and refuses anything unlisted. The deny list stays as a
second layer.

``audit`` answers the question that was never asked before the incident: given the ambient allow rules, which
write-capable tools can this lane actually reach? Tool rules look like ``Name`` or ``Name(scope)``; a bare name in
the deny list covers every scoped form.
"""
from __future__ import annotations

from dataclasses import dataclass

ALLOWLIST = "allowlist"   # the job sees only `allow`; ambient settings do not apply
DENYLIST = "denylist"     # the job inherits ambient settings minus `deny` (the shape that leaked)


@dataclass(frozen=True)
class Lane:
    name: str
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    boundary: str = ALLOWLIST
    restricted: bool = True     # a permission grant on a job in a restricted lane is refused, never re-run wider

    def __post_init__(self):
        if self.boundary not in (ALLOWLIST, DENYLIST):
            raise ValueError(f"boundary must be {ALLOWLIST!r} or {DENYLIST!r}")


def tool_of(rule: str) -> str:
    return rule.split("(", 1)[0].strip()


def is_scoped(rule: str) -> bool:
    return "(" in rule and rule.rstrip().endswith(")")


def _denied(rule: str, deny: tuple[str, ...]) -> bool:
    return rule in deny or tool_of(rule) in deny


def reachable(lane: Lane, ambient_allow: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Every allow rule a job in this lane can actually use."""
    pool = list(lane.allow) if lane.boundary == ALLOWLIST else list(dict.fromkeys([*ambient_allow, *lane.allow]))
    return [r for r in pool if not _denied(r, lane.deny)]


def audit(lane: Lane, ambient_allow: list[str] | tuple[str, ...], write_capable: set[str]) -> list[str]:
    """Unscoped write-capable rules the lane can reach. Empty means the lane holds. A scoped rule such as
    ``Write(reports/**)`` is a deliberate grant (see write_lane) and is not reported."""
    return [r for r in reachable(lane, ambient_allow) if tool_of(r) in write_capable and not is_scoped(r)]


def detect_sensitive_paths(denials: list[dict], markers: tuple[str, ...] = (".claude",)) -> list[str]:
    """File paths in permission denials that sit under a protected directory (one no grant can open, so offering
    a grant would loop). Either separator matches: a check written with ``os.sep`` silently misses paths that
    arrive with the other one, and it did, on Linux CI."""
    hits = []
    for d in denials:
        tool_input = d.get("tool_input") or {}
        path = tool_input.get("file_path") or tool_input.get("path")
        if not path:
            continue
        norm = "/" + str(path).replace("\\", "/")
        if any(f"/{m}/" in norm for m in markers):
            hits.append(path)
    return hits
