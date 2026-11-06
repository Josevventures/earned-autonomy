import json

import pytest

from earned_autonomy.ledger import Ledger, LedgerError


def rec(i, call="approved"):
    return {"id": f"D{i}", "kind": "decision", "action_type": "A", "proposed": "x", "call": call}


def test_append_is_idempotent_on_id(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    assert led.append(rec(1)) is True
    assert led.append(rec(1, "rejected")) is False
    assert [r["call"] for r in led.records()] == ["approved"]


def test_records_are_one_json_object_per_lf_line(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    led.append(rec(1))
    led.append(rec(2))
    raw = (tmp_path / "l.jsonl").read_bytes()
    assert b"\r\n" not in raw and raw.count(b"\n") == 2
    assert [json.loads(line)["id"] for line in raw.decode().splitlines()] == ["D1", "D2"]


def test_a_torn_last_line_is_skipped_and_sealed_by_the_next_append(tmp_path):
    p = tmp_path / "l.jsonl"
    led = Ledger(p)
    led.append(rec(1))
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        f.write('{"id": "D2", "kind": "deci')            # a crash mid-append
    assert [r["id"] for r in led.records()] == ["D1"]
    led.append(rec(3))
    with pytest.raises(LedgerError):                    # the torn line is now a sealed bad line: loud, not silent
        led.records()


def test_a_malformed_line_in_the_middle_fails_loudly(tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_text('{"broken\n' + json.dumps({**rec(1), "at": "t"}) + "\n", encoding="utf-8")
    with pytest.raises(LedgerError):
        Ledger(p).records()


@pytest.mark.parametrize("bad", [{"call": "maybe"}, {"kind": "vote"}, {"action_type": ""}])
def test_invalid_records_are_refused(tmp_path, bad):
    with pytest.raises(LedgerError):
        Ledger(tmp_path / "l.jsonl").append({**rec(1), **bad})
    assert not (tmp_path / "l.jsonl").exists()


def test_a_note_with_table_breaking_characters_survives_verbatim(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    note = 'keep A | drop B, "quoted"\nsecond line'
    led.append({**rec(1), "note": note})
    assert led.records()[0]["note"] == note
