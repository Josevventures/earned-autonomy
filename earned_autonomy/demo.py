"""The bundled scenario, on a synthetic ledger: every name, address and note in it is invented.

Four things happen, each through the same functions the CLI and an integration use:
1. A type reaches 15/15, the agent proposes graduation, the status waits on the user, the user approves.
2. A type with low agreement stays gated however many decisions it logs.
3. A graduated type is overridden twice and demotes itself.
4. A Tier 2 action is refused, and the protected-category guard turns one approved trash into an archive.
"""
from __future__ import annotations

import shutil
import tempfile
from importlib import resources
from pathlib import Path

from . import decisions
from .cli import status_table
from .guard.protected import GuardConfig, guard
from .ledger import Ledger
from .policy import Policy, evaluate, may_act_autonomously

TRASH = "Trash a low-value message"
ARCHIVE = "Archive a promotional message"
SNOOZE = "Snooze a non-urgent thread"
SEND = "Send a message on the user's behalf"


def _step(title: str) -> None:
    print(f"\n== {title} " + "=" * max(0, 74 - len(title)))


def _say(res: decisions.Result) -> None:
    for m in res.messages:
        print(f"   {m}")


def run(keep_dir: str | None = None) -> int:
    work = Path(keep_dir) if keep_dir else Path(tempfile.mkdtemp(prefix="earned-autonomy-demo-"))
    work.mkdir(parents=True, exist_ok=True)
    pkg = resources.files("earned_autonomy") / "examples"
    for name in ("policy.json", "guard.json"):
        (work / name).write_text((pkg / name).read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    (work / "ledger.jsonl").write_text((pkg / "synthetic_ledger.jsonl").read_text(encoding="utf-8"),
                                       encoding="utf-8", newline="\n")
    policy = Policy.from_file(work / "policy.json")
    ledger = Ledger(work / "ledger.jsonl")
    print(f"Synthetic ledger copied to {work}")

    _step("Starting state")
    print(status_table(policy, ledger))

    _step(f"1. '{TRASH}' reaches 15/15")
    _say(decisions.log_decision(ledger, policy, "D-100", TRASH, "Trash 3 messages from deals@shop.example.com",
                                "approved"))
    v = may_act_autonomously(policy, evaluate(policy, ledger.records()), TRASH)
    print(f"   May it act alone yet? {'yes' if v.allowed else 'no'}: {v.reason}")
    print("   The user approves the graduation.")
    _say(decisions.answer_graduation(ledger, policy, TRASH, "approved"))
    v = may_act_autonomously(policy, evaluate(policy, ledger.records()), TRASH)
    print(f"   May it act alone now? {'yes' if v.allowed else 'no'}: {v.reason}")

    _step(f"2. '{ARCHIVE}' stays gated")
    _say(decisions.log_decision(ledger, policy, "D-101", ARCHIVE, "Archive 4 promotional messages", "modified",
                                note="keep the one with a credit"))
    st = evaluate(policy, ledger.records())[ARCHIVE]
    print(f"   {st.name}: {st.agreements}/{len(st.window)} agreed ({round(100 * st.rate)}%), "
          f"status {st.status.value}. More volume does not buy autonomy; agreement does.")

    _step(f"3. '{SNOOZE}' is overridden twice")
    _say(decisions.record_outcome(ledger, policy, "O-100", SNOOZE, "overridden", note="un-snoozed it the same day"))
    st = evaluate(policy, ledger.records())[SNOOZE]
    print(f"   After one override: {st.status.value} (one edge case is not a trend)")
    _say(decisions.record_outcome(ledger, policy, "O-101", SNOOZE, "overridden"))

    _step("4. Tier 2 and the protected-category guard")
    v = may_act_autonomously(policy, evaluate(policy, ledger.records()), SEND)
    print(f"   {'ALLOWED' if v.allowed else 'REFUSED'}: {v.reason}")
    try:
        decisions.answer_graduation(ledger, policy, SEND, "approved")
    except decisions.GuardRefusal as e:
        print(f"   Approving a graduation for it is refused too: {e}")
    cfg = GuardConfig.from_file(work / "guard.json")
    for target in ("deals@shop.example.com", "alerts@examplebank.test"):
        g = guard("trash", target, cfg)
        print(f"   trash {target}: runs as {g.action}" + (f" ({g.reason})" if g.reason else ""))

    _step("Final state")
    print(status_table(policy, ledger))
    print(f"\nLedger: {work / 'ledger.jsonl'} ({len(ledger.records())} records, append-only)")
    if not keep_dir:
        shutil.rmtree(work, ignore_errors=True)
        print("(scratch folder removed; pass --dir to keep it)")
    return 0
