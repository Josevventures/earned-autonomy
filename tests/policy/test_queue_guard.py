"""The unattended transcriber may only apply an answer to a question that was actually asked."""
import pytest

from earned_autonomy import decisions
from earned_autonomy.ledger import Ledger
from earned_autonomy.policy import Policy, Status, Tier, evaluate

T = "Archive a promotional message"
T2 = "Send a message on the user's behalf"


@pytest.fixture
def env(tmp_path):
    return Policy(types={T: Tier.TIER1, T2: Tier.TIER2}), Ledger(tmp_path / "ledger.jsonl")


def test_refuses_an_id_that_was_never_asked_and_writes_nothing(env):
    policy, ledger = env
    with pytest.raises(decisions.GuardRefusal, match="never asked"):
        decisions.log_decision(ledger, policy, "Q-1", T, "archive 8", "approved", via_queue=True)
    assert ledger.records() == []


def test_refuses_a_second_answer_to_the_same_question(env):
    policy, ledger = env
    decisions.ask(ledger, policy, "Q-1", T, "archive 8")
    decisions.log_decision(ledger, policy, "Q-1", T, "archive 8", "approved", via_queue=True)
    with pytest.raises(decisions.GuardRefusal, match="already answered"):
        decisions.log_decision(ledger, policy, "Q-1", T, "archive 8", "rejected", via_queue=True)


def test_an_answered_question_is_logged_with_its_provenance(env):
    policy, ledger = env
    decisions.ask(ledger, policy, "Q-1", T, "archive 8")
    res = decisions.log_decision(ledger, policy, "Q-1", T, "archive 8", "approved", via_queue=True)
    rec = ledger.records()[-1]
    assert rec["source"] == "queue" and rec["answers"] == "Q-1"
    assert res.state.window == [True]


def test_an_ask_is_not_tallied(env):
    policy, ledger = env
    decisions.ask(ledger, policy, "Q-1", T, "archive 8")
    assert evaluate(policy, ledger.records())[T].window == []


def test_the_interactive_path_needs_no_ask(env):
    policy, ledger = env
    res = decisions.log_decision(ledger, policy, "D-1", T, "archive 8", "approved")
    assert res.written and ledger.records()[0]["source"] == "interactive"


def test_re_logging_the_same_interactive_id_changes_nothing(env):
    policy, ledger = env
    decisions.log_decision(ledger, policy, "D-1", T, "archive 8", "approved")
    res = decisions.log_decision(ledger, policy, "D-1", T, "archive 8", "rejected")
    assert not res.written and len(ledger.records()) == 1


def test_an_unknown_type_is_refused_before_anything_is_written(env):
    policy, ledger = env
    with pytest.raises(decisions.UnknownActionType):
        decisions.log_decision(ledger, policy, "D-1", "Not a real type", "x", "approved")
    assert ledger.records() == []


def test_tier2_through_the_queue_is_logged_but_never_tallied(env):
    policy, ledger = env
    decisions.ask(ledger, policy, "Q-2", T2, "reply to a scheduling question")
    res = decisions.log_decision(ledger, policy, "Q-2", T2, "reply", "approved", via_queue=True)
    assert any("Tier 2" in m for m in res.messages)
    st = evaluate(policy, ledger.records())[T2]
    assert st.logged == 1 and st.window == [] and st.status is Status.TIER2


def test_the_users_note_lands_verbatim(env):
    policy, ledger = env
    words = "keep the one with an unused credit | archive the rest"
    decisions.log_decision(ledger, policy, "D-1", T, "archive 9", "modified", note=words)
    assert ledger.records()[0]["note"] == words


def test_an_outcome_on_a_type_that_never_graduated_is_refused(env):
    policy, ledger = env
    with pytest.raises(decisions.GuardRefusal):
        decisions.record_outcome(ledger, policy, "O-1", T, "kept")
