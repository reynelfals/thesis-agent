from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from thesis.recurring import (
    EASTERN,
    attempt_current_slot,
    eligible_slot,
    run_cycle_isolated,
    run_worker,
    slot_run_id,
)
from thesis.store import ThesisStore


class Settings:
    def __init__(self, path: Path, *, allow_execute: bool = False) -> None:
        self.db_path = path
        self.allow_execute = allow_execute

    def assert_paper(self) -> None:
        return None


def cycle_result(decision: str = "blocked", order_id: str = ""):
    return SimpleNamespace(
        decision=decision,
        thesis=SimpleNamespace(order_id=order_id),
    )


def test_slots_align_every_thirty_minutes_and_require_aware_time() -> None:
    day = datetime(2026, 9, 1, tzinfo=EASTERN)
    expected = [
        day.replace(hour=9, minute=35) + timedelta(minutes=30 * index)
        for index in range(13)
    ]

    assert [eligible_slot(value) for value in expected] == expected
    assert eligible_slot(day.replace(hour=9, minute=34, second=59)) is None
    assert eligible_slot(day.replace(hour=15, minute=50)) == expected[-1]
    assert eligible_slot(day.replace(hour=15, minute=50, second=1)) is None
    with pytest.raises(ValueError, match="timezone-aware"):
        eligible_slot(datetime(2026, 9, 1, 9, 35))


def test_slots_are_eastern_and_dst_aware_with_deterministic_ids() -> None:
    winter = datetime(2026, 1, 5, 14, 35, tzinfo=timezone.utc)
    summer = datetime(2026, 7, 6, 13, 35, tzinfo=timezone.utc)

    winter_slot = eligible_slot(winter)
    summer_slot = eligible_slot(summer)

    assert winter_slot is not None and winter_slot.utcoffset() == timedelta(hours=-5)
    assert summer_slot is not None and summer_slot.utcoffset() == timedelta(hours=-4)
    assert slot_run_id(winter_slot) == "thesis-analysis-2026-01-05-0935-et"
    assert slot_run_id(summer_slot) == "thesis-analysis-2026-07-06-0935-et"
    assert eligible_slot(datetime(2026, 7, 5, 13, 35, tzinfo=timezone.utc)) is None


def test_missed_slot_is_not_backfilled(tmp_path) -> None:
    settings = Settings(tmp_path / "audit.sqlite")
    calls = []
    now = datetime(2026, 9, 1, 10, 4, tzinfo=EASTERN)

    result = attempt_current_slot(
        now=now,
        settings_factory=lambda: settings,
        cycle_runner=lambda *_args, **_kwargs: calls.append(True),
    )

    assert result["state"] == "idle"
    assert result["outcome"] == "no_current_slot"
    assert calls == []
    assert (
        ThesisStore(settings.db_path).scheduled_run(
            "thesis-analysis-2026-09-01-0935-et"
        )
        is None
    )


def test_duplicate_and_concurrent_workers_run_slot_once(tmp_path) -> None:
    settings = Settings(tmp_path / "audit.sqlite")
    now = datetime(2026, 9, 1, 10, 5, tzinfo=EASTERN)
    calls = []

    def run(_settings, *, execute):
        calls.append(execute)
        return cycle_result()

    def attempt():
        return attempt_current_slot(
            now=now,
            settings_factory=lambda: settings,
            cycle_runner=run,
            market_open_reader=lambda _settings: True,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: attempt(), range(8)))

    assert calls == [False]
    assert [item["state"] for item in results].count("completed") == 1
    assert [item["state"] for item in results].count("already_claimed") == 7
    record = ThesisStore(settings.db_path).scheduled_run(slot_run_id(now))
    assert record is not None and record["state"] == "completed"


def test_cycle_failure_after_claim_is_terminal(tmp_path) -> None:
    settings = Settings(tmp_path / "audit.sqlite")
    now = datetime(2026, 9, 1, 11, 5, tzinfo=EASTERN)

    first = attempt_current_slot(
        now=now,
        settings_factory=lambda: settings,
        cycle_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("private failure")
        ),
        market_open_reader=lambda _settings: True,
    )
    second = attempt_current_slot(
        now=now + timedelta(minutes=1),
        settings_factory=lambda: settings,
        cycle_runner=lambda *_args, **_kwargs: pytest.fail("must not run twice"),
        market_open_reader=lambda _settings: True,
    )

    assert first["state"] == "failed"
    assert first["outcome"] == "cycle_failed"
    assert "private failure" not in str(first)
    assert second["state"] == "already_claimed"
    assert ThesisStore(settings.db_path).scheduled_run(slot_run_id(now))["state"] == (
        "failed"
    )


def test_worker_stops_gracefully_before_claim(tmp_path) -> None:
    settings = Settings(tmp_path / "audit.sqlite")
    stopped = False

    def stop_after_sleep(_seconds: float) -> None:
        nonlocal stopped
        stopped = True

    result = run_worker(
        now_factory=lambda: datetime(2026, 9, 1, 9, 0, tzinfo=EASTERN),
        sleep=stop_after_sleep,
        should_stop=lambda: stopped,
        settings_factory=lambda: settings,
        cycle_runner=lambda *_args, **_kwargs: pytest.fail("must not run"),
    )

    assert result["state"] == "stopped"
    assert result["outcome"] == "shutdown_requested"


def test_execute_is_always_false_and_enabled_configuration_is_refused(tmp_path) -> None:
    safe = Settings(tmp_path / "safe.sqlite")
    unsafe = Settings(tmp_path / "unsafe.sqlite", allow_execute=True)
    now = datetime(2026, 9, 1, 12, 5, tzinfo=EASTERN)
    calls = []

    completed = attempt_current_slot(
        now=now,
        settings_factory=lambda: safe,
        cycle_runner=lambda value, *, execute: (
            calls.append((value, execute)) or cycle_result()
        ),
        market_open_reader=lambda _settings: True,
    )
    refused = run_worker(
        now_factory=lambda: now,
        settings_factory=lambda: unsafe,
        cycle_runner=lambda *_args, **_kwargs: pytest.fail("must not run"),
    )

    assert completed["state"] == "completed"
    assert completed["execute"] is False
    assert completed["order_submitted"] is False
    assert calls == [(safe, False)]
    assert refused["state"] == "not_armed"
    assert refused["outcome"] == "unsafe_configuration"
    assert ThesisStore(unsafe.db_path).scheduled_run(slot_run_id(now)) is None


def test_closed_market_slot_is_terminally_skipped_before_analysis(tmp_path) -> None:
    settings = Settings(tmp_path / "closed.sqlite")
    now = datetime(2026, 9, 7, 9, 35, tzinfo=EASTERN)  # Labor Day

    result = attempt_current_slot(
        now=now,
        settings_factory=lambda: settings,
        market_open_reader=lambda _settings: False,
        cycle_runner=lambda *_args, **_kwargs: pytest.fail("must not analyze"),
    )

    assert result["state"] == "skipped"
    assert result["outcome"] == "market_closed"
    record = ThesisStore(settings.db_path).scheduled_run(slot_run_id(now))
    assert record is not None and record["state"] == "skipped"
    assert record["outcome"] == "market_closed"


def test_hung_isolated_cycle_times_out_and_next_slot_can_run(tmp_path) -> None:
    settings = Settings(tmp_path / "timeout.sqlite")
    first_slot = datetime(2026, 9, 1, 13, 5, tzinfo=EASTERN)
    second_calls = []

    def hang(_settings, *, execute):
        assert execute is False
        while True:
            pass

    timed_out = attempt_current_slot(
        now=first_slot,
        settings_factory=lambda: settings,
        cycle_runner=hang,
        market_open_reader=lambda _settings: True,
        cycle_executor=run_cycle_isolated,
        timeout_seconds=0.05,
    )
    continued = attempt_current_slot(
        now=first_slot + timedelta(minutes=30),
        settings_factory=lambda: settings,
        cycle_runner=lambda value, *, execute: (
            second_calls.append((value, execute)) or cycle_result("blocked")
        ),
        market_open_reader=lambda _settings: True,
    )

    assert timed_out["state"] == "failed"
    assert timed_out["outcome"] == "cycle_timeout"
    assert ThesisStore(settings.db_path).scheduled_run(slot_run_id(first_slot))[
        "state"
    ] == "failed"
    assert continued["state"] == "completed"
    assert second_calls == [(settings, False)]


def test_worker_moves_to_later_slot_after_bounded_cycle(tmp_path) -> None:
    settings = Settings(tmp_path / "crossing.sqlite")
    moments = iter(
        [
            datetime(2026, 9, 1, 14, 5, tzinfo=EASTERN),
            datetime(2026, 9, 1, 14, 35, tzinfo=EASTERN),
        ]
    )
    executions = []
    sleeps = 0

    def bounded_executor(_runner, _settings, timeout_seconds):
        executions.append(timeout_seconds)
        if len(executions) == 1:
            return {"state": "failed", "outcome": "cycle_timeout", "decision": ""}
        return {"state": "completed", "outcome": "blocked", "decision": "blocked"}

    def stop_after_two_sleeps(_seconds):
        nonlocal sleeps
        sleeps += 1

    result = run_worker(
        now_factory=lambda: next(moments),
        sleep=stop_after_two_sleeps,
        should_stop=lambda: sleeps >= 2,
        settings_factory=lambda: settings,
        market_open_reader=lambda _settings: True,
        cycle_executor=bounded_executor,
        timeout_seconds=9999,
    )

    assert result["state"] == "stopped"
    assert executions == [840, 840]
    assert ThesisStore(settings.db_path).scheduled_run(
        "thesis-analysis-2026-09-01-1405-et"
    )["outcome"] == "cycle_timeout"
    assert ThesisStore(settings.db_path).scheduled_run(
        "thesis-analysis-2026-09-01-1435-et"
    )["state"] == "completed"