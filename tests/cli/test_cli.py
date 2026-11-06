import json
import subprocess
import sys
import time
from importlib import resources

import pytest


def cli(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "earned_autonomy", *args], capture_output=True, text=True,
                          encoding="utf-8", cwd=cwd, timeout=120)


@pytest.fixture
def work(tmp_path):
    pkg = resources.files("earned_autonomy") / "examples"
    (tmp_path / "policy.json").write_text((pkg / "policy.json").read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def test_demo_shows_all_four_outcomes_in_under_a_minute(tmp_path):
    start = time.monotonic()
    r = cli("demo", "--dir", str(tmp_path / "demo"))
    assert r.returncode == 0, r.stderr
    assert time.monotonic() - start < 60
    out = r.stdout
    assert "GRADUATION PROPOSED" in out and "May it act alone yet? no" in out and "GRADUATED" in out
    assert "stays gated" in out and "DEMOTED" in out
    assert "REFUSED: Send a message on the user's behalf: Tier 2" in out
    assert "runs as archive" in out
    assert (tmp_path / "demo" / "ledger.jsonl").exists()


def test_the_synthetic_ledger_is_valid_and_invented():
    from earned_autonomy.ledger import validate
    text = (resources.files("earned_autonomy") / "examples" / "synthetic_ledger.jsonl").read_text(encoding="utf-8")
    for line in text.splitlines():
        rec = json.loads(line)
        validate(rec)
        for addr in [w for w in json.dumps(rec).split() if "@" in w]:
            assert "example" in addr                    # reserved example domains only


def test_status_prints_the_table(work):
    r = cli("status", cwd=work)
    assert r.returncode == 0, r.stderr
    assert "Trash a low-value message" in r.stdout and "Review-required" in r.stdout and "0/15" in r.stdout


def test_log_records_a_decision_and_status_reflects_it(work):
    r = cli("log", "--id", "D-1", "--type", "Archive a promotional message", "--proposed", "archive 4",
            "--call", "approved", cwd=work)
    assert r.returncode == 0, r.stderr
    assert "1 in window, agreement 100%" in r.stdout
    assert "1/15" in cli("status", cwd=work).stdout


def test_an_unknown_type_exits_3(work):
    r = cli("log", "--id", "D-1", "--type", "Nope", "--proposed", "x", "--call", "approved", cwd=work)
    assert r.returncode == 3 and "add it there first" in r.stderr


def test_a_queue_answer_with_no_ask_exits_4(work):
    r = cli("log", "--id", "Q-1", "--type", "Archive a promotional message", "--proposed", "x", "--call",
            "approved", "--via-queue", cwd=work)
    assert r.returncode == 4 and "never asked" in r.stderr
    assert not (work / "ledger.jsonl").exists()


def test_ask_then_queue_answer(work):
    assert cli("ask", "--id", "Q-1", "--type", "Snooze a non-urgent thread", "--proposed", "snooze 2",
               cwd=work).returncode == 0
    r = cli("log", "--id", "Q-1", "--type", "Snooze a non-urgent thread", "--proposed", "snooze 2", "--call",
            "approved", "--via-queue", cwd=work)
    assert r.returncode == 0, r.stderr


def test_check_refuses_tier2_with_exit_1(work):
    r = cli("check", "--type", "Any financial action", cwd=work)
    assert r.returncode == 1 and r.stdout.startswith("REFUSED")


def test_graduate_without_a_proposal_exits_4(work):
    r = cli("graduate", "--type", "Trash a low-value message", "--call", "approved", cwd=work)
    assert r.returncode == 4


def test_a_missing_policy_exits_5_with_a_message(tmp_path):
    r = cli("status", cwd=tmp_path)
    assert r.returncode == 5 and "policy.json" in r.stderr


def test_help_runs():
    assert cli("--help").returncode == 0
