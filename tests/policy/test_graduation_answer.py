"""Regression guard for "the approved graduation that never reached the tier map".

In the system this was extracted from, the user approved a graduation through an unattended channel. The
transcriber ran the ordinary decision path, which (1) logged the answer as one more approved decision, so the
graduation answer was tallied as one of the 15 decisions it was judging, (2) printed "graduation candidate"
again, and (3) never changed the status, which only an interactive turn could do. The type sat at review-required
for six days after the user said yes.

The fix is structural: a graduation answer is its own record kind, never tallied, and status is derived from the
ledger on every read, so an answer that is in the log cannot fail to apply.
"""
import pytest

from earned_autonomy import decisions
from earned_autonomy.ledger import Ledger
from earned_autonomy.policy import Policy, Status, Tier, evaluate

T = "Trash a low-value message"


@pytest.fixture
def env(tmp_path):
    policy = Policy(types={T: Tier.TIER1})
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for i in range(15):
        res = decisions.log_decision(ledger, policy, f"D{i}", T, "trash 3", "approved")
    return policy, ledger, res


def test_the_agent_proposes_at_15_of_15_and_does_not_self_grant(env):
    policy, ledger, res = env
    assert res.state.status is Status.PENDING
    assert any("GRADUATION PROPOSED" in m for m in res.messages)
    assert sum(r["kind"] == "graduation_proposed" for r in ledger.records()) == 1


def test_the_answer_is_not_tallied_as_a_decision(env):
    policy, ledger, _ = env
    before = evaluate(policy, ledger.records())[T]
    decisions.answer_graduation(ledger, policy, T, "approved")
    after = evaluate(policy, ledger.records())[T]
    assert after.window == before.window and after.logged == before.logged == 15
    assert [r["kind"] for r in ledger.records()].count("decision") == 15


def test_an_approval_flips_the_status_with_no_other_write(env):
    policy, ledger, _ = env
    decisions.answer_graduation(ledger, policy, T, "approved")
    # a fresh reader (another process, the next morning) sees the same status: nothing else needed to run
    assert evaluate(policy, Ledger(ledger.path).records())[T].status is Status.AUTONOMOUS


def test_no_second_proposal_after_approval(env):
    policy, ledger, _ = env
    decisions.answer_graduation(ledger, policy, T, "approved")
    res = decisions.log_decision(ledger, policy, "D99", T, "trash 2", "approved")
    assert not any("PROPOSED" in m for m in res.messages)
    assert sum(r["kind"] == "graduation_proposed" for r in ledger.records()) == 1


def test_a_proposal_is_made_once_while_it_waits(env):
    policy, ledger, _ = env
    res = decisions.log_decision(ledger, policy, "D16", T, "trash 2", "approved")
    assert not any("PROPOSED" in m for m in res.messages)
    assert res.state.status is Status.PENDING


def test_an_answer_needs_a_proposal_and_counts_once(env):
    policy, ledger, _ = env
    decisions.answer_graduation(ledger, policy, T, "approved")
    with pytest.raises(decisions.GuardRefusal):
        decisions.answer_graduation(ledger, policy, T, "approved")


def test_a_proposal_cannot_be_answered_before_it_exists(tmp_path):
    policy = Policy(types={T: Tier.TIER1})
    with pytest.raises(decisions.GuardRefusal):
        decisions.answer_graduation(Ledger(tmp_path / "l.jsonl"), policy, T, "approved")
