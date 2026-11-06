"""The graduation and demotion rules, replayed from records."""
import pytest

from earned_autonomy.policy import Policy, Status, Tier, evaluate, may_act_autonomously, wilson_lower

T1 = "Archive a promotional message"
T0 = "Read-only research note"
T2 = "Send a message on the user's behalf"


def policy(**rules):
    return Policy(types={T0: Tier.TIER0, T1: Tier.TIER1, T2: Tier.TIER2}, **rules)


def decs(calls, typ=T1, start=0):
    return [{"id": f"D{start + i}", "kind": "decision", "action_type": typ, "proposed": "x", "call": c,
             "at": "2026-01-01T00:00:00+00:00"} for i, c in enumerate(calls)]


def proposal(pid="P1", typ=T1):
    return {"id": pid, "kind": "graduation_proposed", "action_type": typ, "at": "t"}


def answer(call, pid="P1", typ=T1):
    return {"id": f"{pid}/answer", "kind": "graduation_answer", "action_type": typ, "proposal": pid, "call": call,
            "at": "t"}


def outcome(result, i, typ=T1):
    return {"id": f"O{i}", "kind": "outcome", "action_type": typ, "result": result, "at": "t"}


def graduated(p):
    return decs(["approved"] * 15) + [proposal(), answer("approved")]


def test_defaults_are_90_percent_over_15_with_demotion_after_2():
    p = Policy(types={})
    assert (p.threshold, p.window, p.demote_after) == (0.90, 15, 2)


def test_15_of_15_is_a_candidate_not_autonomous():
    st = evaluate(policy(), decs(["approved"] * 15))[T1]
    assert st.status is Status.CANDIDATE
    assert not may_act_autonomously(policy(), {T1: st, T0: None, T2: None}, T1).allowed


def test_14_of_15_passes_the_90_percent_bar_and_13_does_not():
    assert evaluate(policy(), decs(["modified"] + ["approved"] * 14))[T1].status is Status.CANDIDATE
    assert evaluate(policy(), decs(["modified"] * 2 + ["approved"] * 13))[T1].status is Status.REVIEW


def test_fewer_than_a_full_window_is_never_a_candidate():
    assert evaluate(policy(), decs(["approved"] * 14))[T1].status is Status.REVIEW


def test_the_window_rolls():
    st = evaluate(policy(), decs(["rejected"] * 5 + ["approved"] * 15))[T1]
    assert (len(st.window), st.agreements, st.logged) == (15, 15, 20)


def test_low_agreement_stays_gated_however_many_are_logged():
    calls = (["approved", "modified", "modified", "rejected", "modified", "modified"] * 10)
    st = evaluate(policy(), decs(calls))[T1]
    assert st.status is Status.REVIEW and st.rate < 0.5


def test_approval_makes_it_autonomous():
    st = evaluate(policy(), graduated(policy()))[T1]
    assert st.status is Status.AUTONOMOUS


def test_one_override_does_not_demote_two_do():
    recs = graduated(policy()) + [outcome("overridden", 1)]
    assert evaluate(policy(), recs)[T1].status is Status.AUTONOMOUS
    st = evaluate(policy(), recs + [outcome("overridden", 2)])[T1]
    assert st.status is Status.REVIEW and st.demotions == 1


def test_kept_outcomes_between_overrides_do_not_reset_the_override_count():
    recs = graduated(policy()) + [outcome("overridden", 1), outcome("kept", 2), outcome("overridden", 3)]
    assert evaluate(policy(), recs)[T1].status is Status.REVIEW


def test_a_demotion_starts_the_count_over():
    recs = graduated(policy()) + [outcome("overridden", 1), outcome("overridden", 2)] + decs(["approved"] * 14, start=50)
    assert evaluate(policy(), recs)[T1].status is Status.REVIEW
    recs += decs(["approved"], start=99)
    assert evaluate(policy(), recs)[T1].status is Status.CANDIDATE


def test_a_declined_graduation_starts_the_count_over():
    recs = decs(["approved"] * 15) + [proposal(), answer("rejected")]
    st = evaluate(policy(), recs)[T1]
    assert st.status is Status.REVIEW and st.window == []


def test_demote_after_is_config():
    recs = graduated(policy()) + [outcome("overridden", 1)]
    assert evaluate(policy(demote_after=1), recs)[T1].status is Status.REVIEW


def test_tier0_is_tallied_but_never_a_candidate_and_always_allowed():
    st = evaluate(policy(), decs(["approved"] * 15, typ=T0))
    assert st[T0].status is Status.TIER0 and len(st[T0].window) == 15
    assert may_act_autonomously(policy(), st, T0).allowed


def test_tier2_is_logged_never_tallied_never_allowed():
    st = evaluate(policy(), decs(["approved"] * 30, typ=T2) + [proposal(typ=T2), answer("approved", typ=T2)])
    assert st[T2].logged == 30 and st[T2].window == [] and st[T2].status is Status.TIER2
    v = may_act_autonomously(policy(), st, T2)
    assert not v.allowed and "Tier 2" in v.reason


def test_unknown_type_fails_closed():
    assert not may_act_autonomously(policy(), evaluate(policy(), []), "Delete everything").allowed


def test_type_names_match_case_and_whitespace_insensitively():
    st = evaluate(policy(), decs(["approved"] * 15, typ="  archive A promotional   message "))
    assert st[T1].status is Status.CANDIDATE


def test_a_type_in_two_tiers_is_rejected():
    with pytest.raises(ValueError):
        Policy.from_dict({"types": {"tier1": ["X"], "tier2": ["x"]}})


def test_wilson_lower_bounds_match_the_readme():
    assert round(wilson_lower(15, 15), 2) == 0.80
    assert round(wilson_lower(14, 15), 2) == 0.70
    assert wilson_lower(0, 0) is None
