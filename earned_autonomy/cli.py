"""Command line: ``python -m earned_autonomy <command>``.

  demo      run the bundled scenario on a synthetic ledger in a scratch folder (under a minute, no network)
  status    the tier table for a ledger
  log       record the user's call on a proposed action
  ask       record a question sent through an unattended channel (pairs with ``log --via-queue``)
  outcome   record what the user did after an autonomous action (kept or overridden)
  graduate  record the user's answer to a graduation proposal
  check     may the agent take this action on its own? (exit 0 yes, 1 no)

Exit codes: 0 ok, 1 refused by ``check``, 2 bad arguments, 3 unknown action type, 4 guard refusal,
5 unreadable ledger or policy.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import decisions
from .guard.protected import GuardConfig, guard
from .ledger import Ledger, LedgerError
from .policy import Policy, Tier, evaluate, may_act_autonomously


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{round(100 * x)}%"


def status_table(policy: Policy, ledger: Ledger) -> str:
    states = evaluate(policy, ledger.records())
    rows = [("Action type", "Tier", "Status", "Window", "Agreement", "Wilson 95% low")]
    for st in states.values():
        if st.tier is Tier.TIER2:
            rows.append((st.name, "2", st.status.value, f"{st.logged} logged", "-", "-"))
            continue
        rows.append((st.name, str(int(st.tier)), st.status.value, f"{len(st.window)}/{policy.window}",
                     _pct(st.rate), _pct(st.wilson_lower())))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows]
    lines.insert(1, "  ".join("-" * w for w in widths))
    rule = (f"Graduation: >= {round(policy.threshold * 100)}% agreement over the last {policy.window} decisions, "
            f"then the user approves. Demotion: {policy.demote_after} overridden calls. Tier 2 never graduates.")
    return "\n".join(lines + ["", rule])


def _load(args) -> tuple[Policy, Ledger]:
    return Policy.from_file(args.policy), Ledger(args.ledger)


def _print(res: decisions.Result) -> None:
    for m in res.messages:
        print(m)
    st = res.state
    if st is not None and st.tier is not Tier.TIER2:
        print(f"{st.name}: {st.status.value}, {len(st.window)} in window, agreement {_pct(st.rate)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="earned-autonomy", description="Per-action-type autonomy, earned from "
                                 "logged human decisions.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--ledger", default="ledger.jsonl", help="append-only JSONL ledger (default: %(default)s)")
        p.add_argument("--policy", default="policy.json", help="tiers and rules (default: %(default)s)")
        return p

    d = sub.add_parser("demo", help="run the bundled scenario on synthetic data")
    d.add_argument("--dir", help="keep the demo files here instead of a temporary folder")
    common(sub.add_parser("status", help="print the tier table"))
    p = common(sub.add_parser("log", help="record the user's call on a proposed action"))
    p.add_argument("--id", required=True)
    p.add_argument("--type", required=True)
    p.add_argument("--proposed", required=True)
    p.add_argument("--call", required=True, choices=["approved", "rejected", "modified"])
    p.add_argument("--note", default="")
    p.add_argument("--via-queue", action="store_true", help="unattended transcriber: the id must have been asked")
    p = common(sub.add_parser("ask", help="record a question sent through an unattended channel"))
    p.add_argument("--id", required=True)
    p.add_argument("--type", required=True)
    p.add_argument("--proposed", required=True)
    p = common(sub.add_parser("outcome", help="record the user's reaction to an autonomous action"))
    p.add_argument("--id", required=True)
    p.add_argument("--type", required=True)
    p.add_argument("--result", required=True, choices=["kept", "overridden"])
    p.add_argument("--note", default="")
    p = common(sub.add_parser("graduate", help="record the user's answer to a graduation proposal"))
    p.add_argument("--type", required=True)
    p.add_argument("--call", required=True, choices=["approved", "rejected"])
    p.add_argument("--note", default="")
    p = common(sub.add_parser("check", help="may the agent act on its own? exit 0 yes, 1 no"))
    p.add_argument("--type", required=True)
    p.add_argument("--action", help="the concrete action, e.g. trash (with --target and --guard)")
    p.add_argument("--target", help="the address the action touches")
    p.add_argument("--guard", help="protected-categories config (JSON)")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "demo":
            from .demo import run
            return run(args.dir)
        policy, ledger = _load(args)
        if args.cmd == "status":
            print(status_table(policy, ledger))
        elif args.cmd == "log":
            _print(decisions.log_decision(ledger, policy, args.id, args.type, args.proposed, args.call, args.note,
                                          via_queue=args.via_queue))
        elif args.cmd == "ask":
            _print(decisions.ask(ledger, policy, args.id, args.type, args.proposed))
        elif args.cmd == "outcome":
            _print(decisions.record_outcome(ledger, policy, args.id, args.type, args.result, args.note))
        elif args.cmd == "graduate":
            _print(decisions.answer_graduation(ledger, policy, args.type, args.call, note=args.note))
        elif args.cmd == "check":
            v = may_act_autonomously(policy, evaluate(policy, ledger.records()), args.type)
            print(("ALLOWED: " if v.allowed else "REFUSED: ") + v.reason)
            if v.allowed and args.action and args.target and args.guard:
                g = guard(args.action, args.target, GuardConfig.from_file(args.guard))
                print(f"run: {g.action}" + (f" ({g.reason})" if g.reason else ""))
            return 0 if v.allowed else 1
        return 0
    except decisions.UnknownActionType as e:
        print(f"error: {e.args[0]}", file=sys.stderr)
        return 3
    except decisions.GuardRefusal as e:
        print(f"error: {e}", file=sys.stderr)
        return 4
    except (LedgerError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())
