import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core import ssm
from app.db.models import AgentRun, AgentSettings, GmailCredential, User
from app.jobs import run_digest
from app.jobs.run_digest import due_users

A, B = uuid.uuid4(), uuid.uuid4()


def utc(*args):
    return datetime(*args, tzinfo=UTC)


# --- due_users (pure) ---

def test_notify_hour_boundary_new_york():
    rows = [(A, "America/New_York", 7, None)]
    assert due_users(utc(2026, 11, 16, 11, 59), rows) == []  # 06:59 EST
    assert due_users(utc(2026, 11, 16, 12, 0), rows) == [A]  # 07:00 EST
    assert due_users(utc(2026, 11, 17, 4, 30), rows) == [A]  # 23:30 EST, missed every earlier tick


def test_defaults_when_no_settings_row():
    rows = [(A, None, None, None)]
    assert due_users(utc(2026, 7, 1, 10, 59), rows) == []  # 06:59 EDT
    assert due_users(utc(2026, 7, 1, 11, 0), rows) == [A]  # 07:00 EDT


def test_already_ran_today_skips_and_tomorrow_runs():
    ran = utc(2026, 11, 16, 12, 5)  # 07:05 EST
    rows = [(A, "America/New_York", 7, ran)]
    assert due_users(utc(2026, 11, 16, 20, 0), rows) == []
    assert due_users(utc(2026, 11, 17, 4, 59), rows) == []  # 23:59 EST same local day
    assert due_users(utc(2026, 11, 17, 12, 0), rows) == [A]


def test_ran_yesterday_late_utc_is_not_today():
    # 23:30 EST on the 15th is 04:30 UTC on the 16th: same UTC date as the 16th's tick, different local date.
    rows = [(A, "America/New_York", 7, utc(2026, 11, 16, 4, 30))]
    assert due_users(utc(2026, 11, 16, 12, 0), rows) == [A]


def test_naive_sqlite_timestamp_treated_as_utc():
    # 04:30 UTC is 23:30 EST on the 15th; read as machine-local time it could land on the 16th.
    rows = [(A, "America/New_York", 7, datetime(2026, 11, 16, 4, 30))]
    assert due_users(utc(2026, 11, 16, 12, 0), rows) == [A]


def test_other_timezone():
    rows = [(A, "Asia/Kolkata", 7, None), (B, "America/Los_Angeles", 7, None)]
    now = utc(2026, 11, 16, 1, 30)  # 07:00 IST, 17:30 PST on the 15th
    assert due_users(now, rows) == [A, B]
    assert due_users(utc(2026, 11, 16, 1, 29), rows) == [B]  # 06:59 IST; LA is still past 7 on its 15th


def test_dst_spring_forward_skipped_hour_runs_at_next_tick():
    # 2026-03-08: New York clocks jump 02:00 EST -> 03:00 EDT, so 02:xx never happens.
    rows = [(A, "America/New_York", 2, None)]
    assert due_users(utc(2026, 3, 8, 6, 30), rows) == []  # 01:30 EST
    assert due_users(utc(2026, 3, 8, 7, 0), rows) == [A]  # 03:00 EDT


def test_dst_fall_back_repeated_hour_runs_once():
    # 2026-11-01: 01:xx happens twice (EDT then EST).
    first, second = utc(2026, 11, 1, 5, 0), utc(2026, 11, 1, 6, 0)
    rows = [(A, "America/New_York", 1, None)]
    assert due_users(first, rows) == [A]
    assert due_users(second, [(A, "America/New_York", 1, first)]) == []


def _simulate_day(tz, hour, day_start_utc, fail_ticks=()):
    """24 hourly ticks; returns the tick indexes where a successful run happened."""
    last_ok, ok_ticks = None, []
    for i in range(24):
        now = day_start_utc + timedelta(hours=i, minutes=1)
        if due_users(now, [(A, tz, hour, last_ok)]) == [A] and i not in fail_ticks:
            last_ok = now
            ok_ticks.append(i)
    return ok_ticks


@pytest.mark.parametrize("day", [utc(2026, 11, 16, 5), utc(2026, 3, 8, 5), utc(2026, 11, 1, 4)])
def test_24_ticks_exactly_one_success_per_local_day(day):
    # day = local midnight in New York (EST, spring-forward day, fall-back day)
    assert len(_simulate_day("America/New_York", 7, day)) == 1


def test_failed_run_retried_next_tick():
    day = utc(2026, 11, 16, 5)  # 00:00 EST
    assert _simulate_day("America/New_York", 7, day, fail_ticks={7, 8}) == [9]


# --- main(): query, lock, wiring (SQLite stands in for Postgres) ---

NOW = utc(2026, 11, 16, 12)


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


@pytest.fixture
def job(db, monkeypatch):
    calls = []

    def fake_run(session, uid):
        run = AgentRun(user_id=uid, status="success", started_at=NOW)
        session.add(run)
        session.commit()
        calls.append(uid)
        return run

    monkeypatch.setattr(run_digest, "SessionLocal", db)
    monkeypatch.setattr(run_digest, "run_for_user", fake_run)
    monkeypatch.setattr(run_digest, "try_lock", lambda s: True)
    monkeypatch.setattr(run_digest, "datetime", FixedClock)
    monkeypatch.setattr(sys, "argv", ["run_digest"])
    return calls


def _user(s, email, reauth=False, settings=None):
    u = User(email=email)
    s.add(u)
    s.flush()
    s.add(GmailCredential(user_id=u.id, refresh_token_encrypted=b"x", needs_reauth=reauth))
    if settings:
        s.add(AgentSettings(user_id=u.id, timezone=settings[0], notify_hour_local=settings[1]))
    return u.id


def test_candidates_query(db):
    with db() as s:
        a = _user(s, "a@x.com", settings=("Asia/Kolkata", 9))
        b = _user(s, "b@x.com")
        _user(s, "reauth@x.com", reauth=True)
        s.add(User(email="nocred@x.com"))
        s.add_all([AgentRun(user_id=a, status="failed", started_at=utc(2026, 11, 16, 9)),
                   AgentRun(user_id=a, status="partial", started_at=utc(2026, 11, 15, 9)),
                   AgentRun(user_id=a, status="success", started_at=utc(2026, 11, 14, 9))])
        s.commit()
        got = {r[0]: r[1:] for r in run_digest.candidates(s)}
    assert set(got) == {a, b}
    assert got[a][:2] == ("Asia/Kolkata", 9)
    assert got[a][2].replace(tzinfo=UTC) == utc(2026, 11, 15, 9)
    assert got[b] == (None, None, None)


def test_main_runs_due_users_then_skips_them(job, db):
    with db() as s:
        a = _user(s, "a@x.com")  # no settings row: 07:00 New York, and NOW is 07:00 EST
        _user(s, "late@x.com", settings=("UTC", 13))
        s.commit()
    assert run_digest.main() == 0
    assert job == [a]
    assert run_digest.main() == 0
    assert job == [a]


def test_overlapping_job_exits_cleanly(job, monkeypatch):
    monkeypatch.setattr(run_digest, "try_lock", lambda s: False)
    assert run_digest.main() == 0
    assert job == []


def test_email_override_bypasses_gating_and_lock(job, db, monkeypatch):
    monkeypatch.setattr(run_digest, "try_lock", lambda s: pytest.fail("manual runs take no lock"))
    with db() as s:
        a = _user(s, "a@x.com", settings=("UTC", 23))
        s.add(AgentRun(user_id=a, status="success"))
        s.commit()
    monkeypatch.setattr(sys, "argv", ["run_digest", "--email", " A@x.com "])
    assert run_digest.main() == 0
    assert job == [a]


# --- SSM config loading ---

class FakeSSM:
    def __init__(self, pages):
        self.pages, self.kwargs = pages, None

    def get_paginator(self, op):
        assert op == "get_parameters_by_path"
        return self

    def paginate(self, **kwargs):
        self.kwargs = kwargs
        return self.pages


def test_ssm_loads_all_pages_and_env_wins(monkeypatch):
    monkeypatch.setenv("SSM_PREFIX", "/email-triage/prod")
    monkeypatch.setenv("LLM_MODEL", "from-env")
    monkeypatch.delenv("SSM_TEST_A", raising=False)
    monkeypatch.delenv("SSM_TEST_B", raising=False)
    fake = FakeSSM([
        {"Parameters": [{"Name": "/email-triage/prod/SSM_TEST_A", "Value": "a"},
                        {"Name": "/email-triage/prod/LLM_MODEL", "Value": "from-ssm"}]},
        {"Parameters": [{"Name": "/email-triage/prod/SSM_TEST_B", "Value": "b"}]},
    ])
    ssm.load_into_env(fake)
    assert fake.kwargs == {"Path": "/email-triage/prod/", "WithDecryption": True}
    assert (os.environ["SSM_TEST_A"], os.environ["SSM_TEST_B"], os.environ["LLM_MODEL"]) == ("a", "b", "from-env")


def test_ssm_rejects_placeholder(monkeypatch):
    monkeypatch.setenv("SSM_PREFIX", "/email-triage/prod/")
    monkeypatch.delenv("SSM_TEST_A", raising=False)
    fake = FakeSSM([{"Parameters": [{"Name": "/email-triage/prod/SSM_TEST_A", "Value": "CHANGE_ME"}]}])
    with pytest.raises(RuntimeError, match="SSM_TEST_A"):
        ssm.load_into_env(fake)


def test_ssm_unset_is_noop(monkeypatch):
    monkeypatch.delenv("SSM_PREFIX", raising=False)
    ssm.load_into_env(object())  # would fail if touched
