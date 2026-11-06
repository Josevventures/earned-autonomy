"""The write path: every way a record gets into the ledger, with the guards that keep it honest.

- ``ask`` records a question the agent sent through an unattended channel.
- ``log_decision`` records the user's call. With ``via_queue=True`` (an unattended transcriber, such as a poller
  reading answers off a message queue) it refuses any id that was never asked: the transcriber may apply an
  answer to a question that demonstrably went out, and nothing else.
- ``record_outcome`` records what the user did after an autonomous action. Overrides drive demotion.
- ``answer_graduation`` records the user's yes or no on a graduation proposal. It is its own record kind, so it is
  never counted as one of the decisions it is judging, and the status change is derived from it on replay.

Proposals are made by the agent, automatically, the moment a type becomes a candidate. Approval is never automatic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ledger import Ledger, now_iso
from .policy import Policy, Status, Tier, TypeState, evaluate


class UnknownActionType(LookupError):
    """The policy has no such type. Adding one is a design decision, not something a log call invents."""


class GuardRefusal(PermissionError):
    """A guarded write was refused before anything was written."""


@dataclass
class Result:
    state: TypeState | None
    messages: list[str] = field(default_factory=list)
    written: bool = False


def _resolve(policy: Policy, action_type: str) -> tuple[str, Tier]:
    hit = policy.lookup(action_type)
    if hit is None:
        raise UnknownActionType(f"no action type {action_type!r} in the policy; add it there first "
                                f"(known: {', '.join(policy.types)})")
    return hit


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:40]


def ask(ledger: Ledger, policy: Policy, ask_id: str, action_type: str, proposed: str) -> Result:
    name, _ = _resolve(policy, action_type)
    wrote = ledger.append({"id": ask_id, "kind": "ask", "action_type": name, "proposed": proposed})
    return Result(None, [f"asked {ask_id}" if wrote else f"{ask_id} was already asked"], wrote)


def log_decision(ledger: Ledger, policy: Policy, decision_id: str, action_type: str, proposed: str, call: str,
                 note: str = "", via_queue: bool = False, at: str | None = None) -> Result:
    name, tier = _resolve(policy, action_type)
    records = ledger.records()
    if via_queue:
        asked = next((r for r in records if r["id"] == decision_id), None)
        if asked is None or asked["kind"] != "ask":
            raise GuardRefusal(f"queue guard: {decision_id} was never asked; an unattended transcriber may only "
                               "apply an answer to a question that was sent")
        if any(r.get("answers") == decision_id for r in records):
            raise GuardRefusal(f"queue guard: {decision_id} was already answered")
        decision_id = f"{decision_id}/answer"
    rec = {"id": decision_id, "kind": "decision", "action_type": name, "proposed": proposed, "call": call,
           "note": note, "source": "queue" if via_queue else "interactive", "at": at or now_iso()}
    if via_queue:
        rec["answers"] = decision_id.removesuffix("/answer")
    wrote = ledger.append(rec)
    res = Result(None, [], wrote)
    if not wrote:
        res.messages.append(f"{decision_id} is already logged; nothing changed")
    return _after_write(ledger, policy, name, tier, res)


def record_outcome(ledger: Ledger, policy: Policy, outcome_id: str, action_type: str, result: str,
                   note: str = "", at: str | None = None) -> Result:
    name, tier = _resolve(policy, action_type)
    st = evaluate(policy, ledger.records())[name]
    if st.status not in (Status.AUTONOMOUS, Status.TIER0) and ledger.find(outcome_id) is None:
        raise GuardRefusal(f"{name} is {st.status.value}: it does not act on its own, so there is no autonomous "
                           "outcome to record; log a decision instead")
    before = st.demotions
    wrote = ledger.append({"id": outcome_id, "kind": "outcome", "action_type": name, "result": result,
                           "note": note, "at": at or now_iso()})
    res = _after_write(ledger, policy, name, tier, Result(None, [], wrote))
    if res.state and res.state.demotions > before:
        res.messages.append(f"DEMOTED: {name} had {policy.demote_after} overridden calls and is back to "
                            "review-required. The count starts over.")
    return res


def answer_graduation(ledger: Ledger, policy: Policy, action_type: str, call: str, by: str = "user",
                      note: str = "", at: str | None = None) -> Result:
    name, tier = _resolve(policy, action_type)
    st = evaluate(policy, ledger.records())[name]
    if st.pending_proposal is None:
        raise GuardRefusal(f"{name} has no graduation proposal waiting for an answer (status: {st.status.value})")
    wrote = ledger.append({"id": f"{st.pending_proposal}/answer", "kind": "graduation_answer", "action_type": name,
                           "proposal": st.pending_proposal, "call": call, "by": by, "note": note,
                           "at": at or now_iso()})
    res = _after_write(ledger, policy, name, tier, Result(None, [], wrote))
    if call == "approved":
        res.messages.append(f"GRADUATED: {name} now runs on its own. {policy.demote_after} overridden calls send "
                            "it back to review.")
    else:
        res.messages.append(f"Declined: {name} stays review-required and the count starts over.")
    return res


def _after_write(ledger: Ledger, policy: Policy, name: str, tier: Tier, res: Result) -> Result:
    states = evaluate(policy, ledger.records())
    st = states[name]
    if tier is Tier.TIER2:
        res.messages.append(f"{name}: Tier 2, logged only (never tallied, never graduates)")
    elif st.status is Status.CANDIDATE:
        earlier = sum(1 for r in ledger.records() if r["kind"] == "graduation_proposed" and r["action_type"] == name)
        prop_id = f"GRAD-{_slug(name)}-{earlier + 1}"
        ledger.append({"id": prop_id, "kind": "graduation_proposed", "action_type": name,
                       "evidence": f"{st.agreements}/{len(st.window)} agreed"})
        st = evaluate(policy, ledger.records())[name]
        res.messages.append(f"GRADUATION PROPOSED ({prop_id}): {name} agreed {st.agreements}/{len(st.window)}. "
                            "Waiting for the user's approval; the agent never self-promotes.")
    res.state = st
    return res
