from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from thesis.scheduled import (
    EASTERN,
    MONDAY_RUN_ID,
    MONDAY_TARGET,
    MONDAY_WINDOW_END,
    attempt_scheduled_cycle,
    schedule_phase,
    wait_and_run,
)
from thesis.store import ThesisStore


class Settings:
    def __init__(self, path: Path, *, allow_execute: bool = True) -> None:
        self.db_path = path
        self.allow_execute = allow_execute
        self.paper_checks = 0

    def assert_paper(self) -> None:
        self.paper_checks += 1


def result(*, decision: str = "blocked", order_id: str = ""):
    return SimpleNamespace(
        decision=decision,
        thesis=SimpleNamespace(order_id=order_id),
    )


def test_schedule_uses_eastern_time_and_requires_aware_clock() -> None:
    assert schedule_phase(MONDAY_TARGET.astimezone(timezone.utc)) == "due"
    assert schedule_phase(MONDAY_TARGET - timedelta(seconds=1)) == "waiting"
    assert schedule_phase(MONDAY_WINDOW_END) == "due"
    assert schedule_phase(MONDAY_WINDOW_END + timedelta(seconds=1)) == "expired"
    with pytest.raises(ValueError, match="timezone-aware"):
        schedule_phase(datetime(2026, 8, 31, 9, 35))


def test_early_attempt_waits_without_claiming(tmp_path) -> None:
    settings = Settings(tmp_path / "thesis.sqlite")
    calls = []

    outcome = attempt_scheduled_cycle(
        now=MONDAY_TARGET - timedelta(minutes=1),
        settings_factory=lambda: settings,
        cycle_runner=lambda *_args, **_kwargs: calls.append(True),
    )

    assert outcome["state"] == "waiting"
    assert calls == []
    assert ThesisStore(settings.db_path).scheduled_run(MONDAY_RUN_ID) is None


def test_due_attempt_claims_and_runs_exactly_once_from_utc(tmp_path) -> None:
    settings = Settings(tmp_path / "thesis.sqlite")
    calls = []

    def run(value, *, execute):
        calls.append((value, execute))
        return result(decision="submitted", order_id="paper-order")

    first = attempt_scheduled_cycle(
        now=MONDAY_TARGET.astimezone(timezone.utc),
        settings_factory=lambda: settings,
        cycle_runner=run,
    )
    second = attempt_scheduled_cycle(
        now=MONDAY_TARGET + timedelta(minutes=1),
        settings_factory=lambda: settings,
        cycle_runner=run,
    )

    assert calls == [(settings, True)]
    assert first["state"] == "completed"
    assert first["decision"] == "submitted"
    assert first["order_submitted"] is True
    assert second["state"] == "already_claimed"
    record = ThesisStore(settings.db_path).scheduled_run(MONDAY_RUN_ID)
    assert record is not None
    assert record["state"] == "completed"
    assert record["outcome"] == "submitted"


def test_failure_after_claim_is_terminal_and_sanitized(tmp_path) -> None:
    settings = Settings(tmp_path / "thesis.sqlite")
    secret = "broker-secret-that-must-not-be-logged"
    calls = []

    def fail(*_args, **_kwargs):
        calls.append(True)
        raise RuntimeError(secret)

    first = attempt_scheduled_cycle(
        now=MONDAY_TARGET,
        settings_factory=lambda: settings,
        cycle_runner=fail,
    )
    second = attempt_scheduled_cycle(
        now=MONDAY_TARGET + timedelta(minutes=1),
        settings_factory=lambda: settings,
        cycle_runner=fail,
    )

    assert calls == [True]
    assert first["state"] == "failed"
    assert first["outcome"] == "cycle_failed"
    assert secret not in str(first)
    assert second["state"] == "already_claimed"
    assert ThesisStore(settings.db_path).scheduled_run(MONDAY_RUN_ID)["state"] == "failed"


def test_expired_window_is_recorded_and_never_runs(tmp_path) -> None:
    settings = Settings(tmp_path / "thesis.sqlite")
    calls = []

    first = attempt_scheduled_cycle(
        now=MONDAY_WINDOW_END + timedelta(seconds=1),
        settings_factory=lambda: settings,
        cycle_runner=lambda *_args, **_kwargs: calls.append(True),
    )
    second = attempt_scheduled_cycle(
        now=MONDAY_WINDOW_END + timedelta(minutes=2),
        settings_factory=lambda: settings,
        cycle_runner=lambda *_args, **_kwargs: calls.append(True),
    )

    assert calls == []
    assert first["state"] == "skipped"
    assert first["outcome"] == "window_expired"
    assert second["state"] == "already_claimed"


def test_execution_must_be_enabled_before_cycle_and_failure_is_terminal(
    tmp_path,
) -> None:
    settings = Settings(tmp_path / "thesis.sqlite", allow_execute=False)
    calls = []

    first = attempt_scheduled_cycle(
        now=MONDAY_TARGET,
        settings_factory=lambda: settings,
        cycle_runner=lambda *_args, **_kwargs: calls.append(True),
    )
    second = attempt_scheduled_cycle(
        now=MONDAY_TARGET + timedelta(minutes=1),
        settings_factory=lambda: settings,
        cycle_runner=lambda *_args, **_kwargs: calls.append(True),
    )

    assert calls == []
    assert first["state"] == "failed"
    assert first["outcome"] == "execution_not_enabled"
    assert second["state"] == "already_claimed"


def test_waiter_announces_once_then_runs_when_due(tmp_path) -> None:
    settings = Settings(tmp_path / "thesis.sqlite")
    moments = iter(
        [
            MONDAY_TARGET - timedelta(seconds=31),
            MONDAY_TARGET - timedelta(seconds=1),
            MONDAY_TARGET,
        ]
    )
    sleeps = []
    emitted = []
    calls = []

    outcome = wait_and_run(
        now_factory=lambda: next(moments),
        sleep=sleeps.append,
        settings_factory=lambda: settings,
        cycle_runner=lambda value, *, execute: (
            calls.append((value, execute)) or result()
        ),
        emit=emitted.append,
    )

    assert sleeps == [30.0, 1.0]
    assert len(emitted) == 1
    assert emitted[0]["state"] == "waiting"
    assert emitted[0]["outcome"] == "armed"
    assert calls == [(settings, True)]
    assert outcome["state"] == "completed"


def test_waiter_refuses_to_arm_without_process_scoped_execution(tmp_path) -> None:
    settings = Settings(tmp_path / "thesis.sqlite", allow_execute=False)

    outcome = wait_and_run(
        now_factory=lambda: datetime.now(EASTERN),
        sleep=lambda _seconds: None,
        settings_factory=lambda: settings,
        cycle_runner=lambda *_args, **_kwargs: pytest.fail("must not run"),
    )

    assert outcome["state"] == "not_armed"
    assert outcome["outcome"] == "execution_not_enabled"
    assert ThesisStore(settings.db_path).scheduled_run(MONDAY_RUN_ID) is None


def test_waiter_stops_cleanly_before_claim_when_shutdown_is_requested(
    tmp_path,
) -> None:
    settings = Settings(tmp_path / "thesis.sqlite")
    stopped = False
    calls = []

    def request_stop(_seconds: float) -> None:
        nonlocal stopped
        stopped = True

    outcome = wait_and_run(
        now_factory=lambda: MONDAY_TARGET - timedelta(minutes=1),
        sleep=request_stop,
        should_stop=lambda: stopped,
        settings_factory=lambda: settings,
        cycle_runner=lambda *_args, **_kwargs: calls.append(True),
    )

    assert outcome["state"] == "stopped"
    assert outcome["outcome"] == "shutdown_before_claim"
    assert calls == []
    assert ThesisStore(settings.db_path).scheduled_run(MONDAY_RUN_ID) is None


def test_waiter_finishes_claimed_cycle_after_shutdown_request(tmp_path) -> None:
    settings = Settings(tmp_path / "thesis.sqlite")
    stopped = False
    calls = []

    def run(value, *, execute):
        nonlocal stopped
        stopped = True
        calls.append((value, execute))
        return result(decision="submitted", order_id="paper-order")

    outcome = wait_and_run(
        now_factory=lambda: MONDAY_TARGET,
        should_stop=lambda: stopped,
        settings_factory=lambda: settings,
        cycle_runner=run,
    )

    assert calls == [(settings, True)]
    assert outcome["state"] == "completed"
    assert outcome["outcome"] == "submitted"
    assert ThesisStore(settings.db_path).scheduled_run(MONDAY_RUN_ID)["state"] == (
        "completed"
    )