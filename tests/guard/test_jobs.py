import threading

import pytest

from earned_autonomy.adapters import claude_code as cc
from earned_autonomy.guard.jobs import BLOCKED, EXPIRED, WRONG_TOOL, JobBook


@pytest.fixture
def book(tmp_path):
    return JobBook(tmp_path / "jobs.json")


def denied(book, lane=None, tools=("Write",), request="update the notes file"):
    jid = book.create(request, lane)
    book.mark_denied(jid, list(tools))
    return jid


def test_a_grant_on_a_restricted_lane_is_refused_and_nothing_runs(book):
    jid = denied(book, cc.READ_ONLY)
    ran = []
    assert book.grant(jid, "Write", ran.append, lambda r, t: ran.append(r)) == (None, BLOCKED)
    assert ran == []


@pytest.mark.parametrize("job_id", [None, "", "nope"])
def test_an_unknown_job_fails_closed_as_expired(book, job_id):
    assert book.grant_check(job_id) == (None, EXPIRED)


def test_a_legacy_record_without_the_flag_fails_closed(book):
    book._jobs["old"] = {"request": "x", "status": "permission_denied", "deniedTools": ["Write"]}
    assert book.grant_check("old") == (None, EXPIRED)


@pytest.mark.parametrize("status", ["running", "complete", "error", "granted"])
def test_only_a_job_waiting_on_a_permission_can_be_granted(book, status):
    jid = denied(book)
    book.mark_done(jid, status)
    assert book.grant_check(jid) == (None, EXPIRED)


def test_the_rerun_is_the_jobs_own_request(book):
    jid = denied(book, request="the original request")
    seen = []
    book.grant(jid, "Write", lambda t: None, lambda r, t: seen.append(r) or "rerun-1")
    assert seen == ["the original request"]
    assert book.get(jid)["status"] == "granted" and book.get(jid)["rerun"] == "rerun-1"


def test_only_a_tool_the_job_was_denied(book):
    jid = denied(book, tools=("Write",))
    applied = []
    assert book.grant(jid, "Bash", applied.append, lambda r, t: "x") == (None, WRONG_TOOL)
    assert applied == [] and book.get(jid)["status"] == "permission_denied"


def test_a_grant_is_consumed_once(book):
    jid = denied(book)
    assert book.grant(jid, "Write", lambda t: None, lambda r, t: "rerun-1") == ("rerun-1", None)
    assert book.grant(jid, "Write", lambda t: None, lambda r, t: "rerun-2") == (None, EXPIRED)


def test_concurrent_grants_on_one_job_accept_exactly_one(book):
    jid = denied(book)
    gate = threading.Barrier(8)
    results = []

    def go():
        gate.wait()
        results.append(book.claim_grant(jid, "Write")[1])

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert results.count(None) == 1 and results.count(EXPIRED) == 7


@pytest.mark.parametrize("fail_at", ["apply", "rerun"])
def test_any_failure_after_the_claim_expires_the_job(book, fail_at):
    jid = denied(book)

    def boom(*a):
        raise RuntimeError("disk full")

    apply_grant = boom if fail_at == "apply" else (lambda t: None)
    assert book.grant(jid, "Write", apply_grant, boom) == (None, EXPIRED)
    job = book.get(jid)
    assert job["status"] == EXPIRED
    assert (job["grantOutcome"] == "granted_rerun_failed") == (fail_at == "rerun")


def test_a_keyboard_interrupt_still_expires_the_job_and_propagates(book):
    jid = denied(book)

    def interrupt(*a):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        book.grant(jid, "Write", interrupt, interrupt)
    assert book.get(jid)["status"] == EXPIRED


def test_a_restart_expires_waiting_jobs_and_keeps_only_300_characters(book, tmp_path):
    jid = denied(book, request="x" * 1000)
    again = JobBook(tmp_path / "jobs.json")
    again.load()
    assert len(again.get(jid)["request"]) == 300
    assert again.grant_check(jid) == (None, EXPIRED)


def test_a_restricted_job_still_reads_blocked_after_a_restart(book, tmp_path):
    jid = denied(book, cc.READ_ONLY)
    again = JobBook(tmp_path / "jobs.json")
    again.load()
    assert again.grant_check(jid) == (None, BLOCKED)


def test_an_unrestricted_rerun_stays_grantable(book):
    jid = denied(book)
    rerun = []
    book.grant(jid, "Write", lambda t: None, lambda r, t: rerun.append(book.create(r)) or rerun[0])
    book.mark_denied(rerun[0], ["Edit"])
    assert book.grant_check(rerun[0])[1] is None
