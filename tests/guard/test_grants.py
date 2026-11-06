from datetime import datetime, timedelta, timezone

from earned_autonomy.adapters.base import InMemoryBackend
from earned_autonomy.adapters.claude_code import SettingsFileBackend
from earned_autonomy.guard.grants import MAX_TIMER_SECONDS, GrantStore, timer_plan

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


def store(tmp_path, backend=None, clock=None):
    return GrantStore(tmp_path / "grants.json", backend or InMemoryBackend(), clock=clock or Clock(),
                      use_timers=False)


def test_a_grant_that_expired_while_offline_is_revoked_on_startup(tmp_path):
    clock, backend = Clock(), InMemoryBackend()
    store(tmp_path, backend, clock).grant("Write", "weekly report", minutes=60)
    clock.now = T0 + timedelta(hours=3)                    # the process was down past the expiry
    s2 = store(tmp_path, backend, clock)
    assert s2.reconcile() == []
    assert "Write" not in backend.allowed and s2.active() == []
    assert s2.history()[-1]["action"] == "revoked (expired while offline)"


def test_a_live_grant_is_rearmed_with_its_true_remaining_time(tmp_path):
    clock = Clock()
    store(tmp_path, clock=clock).grant("Write", minutes=60)
    clock.now = T0 + timedelta(minutes=45)
    assert store(tmp_path, clock=clock).reconcile() == [("Write", 15 * 60)]


def test_an_indefinite_grant_is_left_alone(tmp_path):
    clock = Clock()
    s = store(tmp_path, clock=clock)
    s.grant("Write", "until revoked")
    clock.now = T0 + timedelta(days=4000)
    s.reconcile()
    assert [g["tool"] for g in s.active()] == ["Write"]


def test_revoking_never_strips_a_rule_the_grant_did_not_add(tmp_path):
    backend = InMemoryBackend({"Read"})                    # already allowed for an unrelated reason
    s = store(tmp_path, backend)
    s.grant("Read", "test", minutes=5)
    s.revoke("Read")
    assert "Read" in backend.allowed
    assert "tracking only" in s.history()[-1]["action"]


def test_a_regrant_keeps_ownership(tmp_path):
    backend = InMemoryBackend()
    s = store(tmp_path, backend)
    s.grant("Write", minutes=5)
    s.grant("Write", minutes=10)                           # backend says "already allowed" the second time
    s.revoke("Write")
    assert "Write" not in backend.allowed


def test_long_timers_are_chained_under_the_platform_cap():
    year = 365 * 24 * 3600
    wait, rest = timer_plan(year)
    assert wait == MAX_TIMER_SECONDS and rest == year - MAX_TIMER_SECONDS
    assert timer_plan(60) == (60, 0)


def test_a_real_timer_arms_without_overflow_for_a_year(tmp_path):
    s = GrantStore(tmp_path / "g.json", InMemoryBackend(), use_timers=True)
    s.grant("Write", minutes=365 * 24 * 60)
    s.revoke("Write")                                      # cancels the timer


def test_settings_file_backend_roundtrip(tmp_path):
    b = SettingsFileBackend(tmp_path / "settings.json")
    assert b.allow("Write") is True and b.allow("Write") is False
    assert b.allow_rules() == ["Write"]
    b.revoke("Write")
    assert b.allow_rules() == []
