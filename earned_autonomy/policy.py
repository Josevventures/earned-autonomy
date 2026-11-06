"""Tiers, the graduation and demotion rules, and the replay that turns a ledger into current status.

The status of an action type is never stored anywhere. It is recomputed from the ledger every time, so an approved
graduation cannot "fail to apply" and a demotion cannot be forgotten: if the record is in the log, the status
follows from it. (See the README, "What the audit trail caught", for the bug that made this the rule.)

Rules, per action type (one type earning trust never implies trust for another):

- Tier 0: always autonomous. Its decisions are tallied for quality only; it is never a graduation candidate.
- Tier 1: starts review-required. When the most recent ``window`` scored records for the type are all on the
  books and at least ``threshold`` of them agree with the user, the type becomes a graduation *candidate*. The
  agent proposes; only the user's explicit approval makes it Autonomous. Never silently.
  On an Autonomous type, ``demote_after`` overridden calls send it back to review-required automatically.
  A declined graduation or a demotion starts the count over: the next candidacy needs a full fresh window.
- Tier 2: external, financial or irreversible. Logged, never tallied, never a candidate, never autonomous.
"""
from __future__ import annotations

import enum
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_WINDOW = 15
DEFAULT_THRESHOLD = 0.90
DEFAULT_DEMOTE_AFTER = 2


class Tier(enum.IntEnum):
    TIER0 = 0
    TIER1 = 1
    TIER2 = 2


class Status(str, enum.Enum):
    TIER0 = "Tier 0, tracked"
    REVIEW = "Review-required"
    CANDIDATE = "Graduation candidate"
    PENDING = "Graduation pending approval"
    AUTONOMOUS = "Autonomous"
    TIER2 = "Tier 2, never graduates"


def normalize(name: str) -> str:
    """Action-type names match case-insensitively and ignore runs of whitespace."""
    return " ".join(str(name or "").split()).casefold()


@dataclass(frozen=True)
class Policy:
    types: dict[str, Tier]                       # display name -> tier
    window: int = DEFAULT_WINDOW
    threshold: float = DEFAULT_THRESHOLD
    demote_after: int = DEFAULT_DEMOTE_AFTER

    def __post_init__(self):
        if self.window < 1:
            raise ValueError("window must be at least 1")
        if not 0 < self.threshold <= 1:
            raise ValueError("threshold must be in (0, 1]")
        if self.demote_after < 1:
            raise ValueError("demote_after must be at least 1")
        seen: dict[str, str] = {}
        for name in self.types:
            key = normalize(name)
            if not key:
                raise ValueError("an action type needs a name")
            if key in seen:
                raise ValueError(f"action type {name!r} is listed twice (also as {seen[key]!r})")
            seen[key] = name

    def lookup(self, action_type: str) -> tuple[str, Tier] | None:
        """(canonical display name, tier), or None for a type the policy does not list. Unknown types are a
        design decision for a human, never something the ledger invents."""
        key = normalize(action_type)
        for name, tier in self.types.items():
            if normalize(name) == key:
                return name, tier
        return None

    @classmethod
    def from_dict(cls, data: dict) -> "Policy":
        types: dict[str, Tier] = {}
        for tier_key, tier in (("tier0", Tier.TIER0), ("tier1", Tier.TIER1), ("tier2", Tier.TIER2)):
            for name in data.get("types", {}).get(tier_key, []):
                if normalize(name) in {normalize(n) for n in types}:
                    raise ValueError(f"action type {name!r} is listed in more than one tier")
                types[name] = tier
        rules = data.get("rules", {})
        return cls(types=types,
                   window=int(rules.get("window", DEFAULT_WINDOW)),
                   threshold=float(rules.get("threshold", DEFAULT_THRESHOLD)),
                   demote_after=int(rules.get("demote_after", DEFAULT_DEMOTE_AFTER)))

    @classmethod
    def from_file(cls, path: str | Path) -> "Policy":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass
class TypeState:
    name: str
    tier: Tier
    status: Status
    window: list[bool] = field(default_factory=list)   # agreement of each scored record in the current window
    logged: int = 0                                    # every decision on this type, all time (Tier 2 included)
    overrides: int = 0                                 # overridden calls since the type last graduated
    demotions: int = 0
    graduations: int = 0
    pending_proposal: str | None = None                # id of an unanswered graduation proposal

    @property
    def agreements(self) -> int:
        return sum(self.window)

    @property
    def rate(self) -> float | None:
        return self.agreements / len(self.window) if self.window else None

    def wilson_lower(self, z: float = 1.96) -> float | None:
        return wilson_lower(self.agreements, len(self.window), z)


def wilson_lower(successes: int, n: int, z: float = 1.96) -> float | None:
    """Lower bound of the Wilson score interval. 15/15 gives about 0.80 and 14/15 about 0.70 at 95%: the 90% rule
    is a policy choice, not a statistical guarantee."""
    if n <= 0:
        return None
    p = successes / n
    z2 = z * z
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (centre - margin) / (1 + z2 / n)


AGREE_CALLS = {"approved"}
DECISION_CALLS = {"approved", "rejected", "modified"}
OUTCOME_RESULTS = {"kept", "overridden"}


def _agrees(rec: dict) -> bool:
    if rec["kind"] == "decision":
        return rec["call"] in AGREE_CALLS
    return rec["result"] == "kept"


def evaluate(policy: Policy, records: list[dict]) -> dict[str, TypeState]:
    """Replay the ledger, in order, into the current state of every action type the policy lists."""
    states = {name: TypeState(name, tier, _initial(tier)) for name, tier in policy.types.items()}
    for rec in records:
        hit = policy.lookup(rec.get("action_type", ""))
        if hit is None:
            continue                                   # a type removed from the policy keeps its history, inert
        st = states[hit[0]]
        kind = rec.get("kind")
        if kind == "decision":
            st.logged += 1
        if st.tier is Tier.TIER2:
            continue                                   # logged, never tallied
        if kind in ("decision", "outcome"):
            agree = _agrees(rec)
            st.window.append(agree)
            st.window[:] = st.window[-policy.window:]
            if st.status is Status.AUTONOMOUS and not agree:
                st.overrides += 1
                if st.overrides >= policy.demote_after:
                    st.status = Status.REVIEW
                    st.demotions += 1
                    st.overrides = 0
                    st.window.clear()                  # a demotion starts the count over
        elif kind == "graduation_proposed" and st.tier is Tier.TIER1 and st.status is not Status.AUTONOMOUS:
            st.pending_proposal = rec["id"]
            st.status = Status.PENDING
        elif kind == "graduation_answer" and st.pending_proposal == rec.get("proposal"):
            # The answer is not a decision about an action, so it never enters the window.
            st.pending_proposal = None
            if rec["call"] == "approved":
                st.status = Status.AUTONOMOUS
                st.graduations += 1
                st.overrides = 0
            else:
                st.status = Status.REVIEW
                st.window.clear()                      # a declined graduation starts the count over
    for st in states.values():
        if st.tier is Tier.TIER1 and st.status is Status.REVIEW and is_candidate(policy, st):
            st.status = Status.CANDIDATE
    return states


def is_candidate(policy: Policy, st: TypeState) -> bool:
    return (st.tier is Tier.TIER1 and len(st.window) == policy.window
            and st.agreements >= math.ceil(policy.threshold * policy.window - 1e-9))


def _initial(tier: Tier) -> Status:
    return {Tier.TIER0: Status.TIER0, Tier.TIER1: Status.REVIEW, Tier.TIER2: Status.TIER2}[tier]


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str


def may_act_autonomously(policy: Policy, states: dict[str, TypeState], action_type: str) -> Verdict:
    """The execution-side question: may the agent take this action without asking? Fails closed."""
    hit = policy.lookup(action_type)
    if hit is None:
        return Verdict(False, f"unknown action type {action_type!r}: ask the user (and add it to the policy)")
    st = states[hit[0]]
    if st.tier is Tier.TIER2:
        return Verdict(False, f"{st.name}: Tier 2 (external, financial or irreversible), needs the user's sign-off "
                              "every time")
    if st.status in (Status.TIER0, Status.AUTONOMOUS):
        return Verdict(True, f"{st.name}: {st.status.value}")
    return Verdict(False, f"{st.name}: {st.status.value}, ask the user")
