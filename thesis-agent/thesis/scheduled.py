from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from thesis.config import Settings, load_settings
from thesis.cycle import CycleResult, run_cycle
from thesis.store import ThesisStore

EASTERN = ZoneInfo("America/New_York")
MONDAY_RUN_ID = "thesis-paper-2026-08-31-0935-et"
MONDAY_TARGET = datetime(2026, 8, 31, 9, 35, tzinfo=EASTERN)
MONDAY_GRACE = timedelta(minutes=15)
MONDAY_WINDOW_END = MONDAY_TARGET + MONDAY_GRACE
POLL_SECONDS = 30.0

SettingsFactory = Callable[[], Settings]
CycleRunner = Callable[..., CycleResult]
StoreFactory = Callable[[Path], ThesisStore]
Emitter = Callable[[dict[str, Any]], None]
StopChecker = Callable[[], bool]


def _require_aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("scheduled cycle clock must be timezone-aware")
    return now.astimezone(EASTERN)


def schedule_phase(now: datetime) -> str:
    local = _require_aware(now)
    if local < MONDAY_TARGET:
        return "waiting"
    if local <= MONDAY_WINDOW_END:
        return "due"
    return "expired"


def seconds_until_target(now: datetime) -> float:
    local = _require_aware(now)
    return max(0.0, (MONDAY_TARGET - local).total_seconds())


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _public_result(
    *,
    state: str,
    outcome: str,
    decision: str = "",
    order_submitted: bool = False,
) -> dict[str, Any]:
    return {
        "event": "monday_paper_cycle",
        "run_id": MONDAY_RUN_ID,
        "state": state,
        "outcome": outcome,
        "decision": decision,
        "order_submitted": order_submitted,
        "target_at": MONDAY_TARGET.isoformat(),
    }


def attempt_scheduled_cycle(
    *,
    now: datetime,
    settings_factory: SettingsFactory = load_settings,
    cycle_runner: CycleRunner = run_cycle,
    store_factory: StoreFactory = ThesisStore,
) -> dict[str, Any]:
    local_now = _require_aware(now)
    settings = settings_factory()
    settings.assert_paper()
    store = store_factory(settings.db_path)
    phase = schedule_phase(local_now)

    if phase == "waiting":
        return _public_result(state="waiting", outcome="not_due")

    claimed = store.claim_scheduled_run(
        run_id=MONDAY_RUN_ID,
        target_at=MONDAY_TARGET.isoformat(),
        claimed_at=_timestamp(local_now),
    )
    if not claimed:
        existing = store.scheduled_run(MONDAY_RUN_ID)
        return _public_result(
            state="already_claimed",
            outcome=(existing or {}).get("state", "claimed"),
        )

    if phase == "expired":
        store.finish_scheduled_run(
            run_id=MONDAY_RUN_ID,
            state="skipped",
            outcome="window_expired",
            finished_at=_timestamp(local_now),
        )
        return _public_result(state="skipped", outcome="window_expired")

    if not settings.allow_execute:
        store.finish_scheduled_run(
            run_id=MONDAY_RUN_ID,
            state="failed",
            outcome="execution_not_enabled",
            finished_at=_timestamp(local_now),
        )
        return _public_result(state="failed", outcome="execution_not_enabled")

    try:
        result = cycle_runner(settings, execute=True)
    except Exception:
        store.finish_scheduled_run(
            run_id=MONDAY_RUN_ID,
            state="failed",
            outcome="cycle_failed",
            finished_at=_timestamp(datetime.now(timezone.utc)),
        )
        return _public_result(state="failed", outcome="cycle_failed")

    decision = str(result.decision or "")
    store.finish_scheduled_run(
        run_id=MONDAY_RUN_ID,
        state="completed",
        outcome=decision or "unknown",
        finished_at=_timestamp(datetime.now(timezone.utc)),
    )
    return _public_result(
        state="completed",
        outcome=decision or "unknown",
        decision=decision,
        order_submitted=bool(result.thesis.order_id),
    )


def wait_and_run(
    *,
    now_factory: Callable[[], datetime] = lambda: datetime.now(EASTERN),
    sleep: Callable[[float], None] = time.sleep,
    should_stop: StopChecker = lambda: False,
    settings_factory: SettingsFactory = load_settings,
    cycle_runner: CycleRunner = run_cycle,
    store_factory: StoreFactory = ThesisStore,
    emit: Emitter | None = None,
) -> dict[str, Any]:
    preflight = settings_factory()
    preflight.assert_paper()
    if not preflight.allow_execute:
        return _public_result(state="not_armed", outcome="execution_not_enabled")
    del preflight

    if should_stop():
        return _public_result(state="stopped", outcome="shutdown_before_claim")

    announced = False
    while True:
        if should_stop():
            return _public_result(state="stopped", outcome="shutdown_before_claim")
        now = now_factory()
        if schedule_phase(now) != "waiting":
            break
        if not announced and emit is not None:
            emit(
                {
                    **_public_result(state="waiting", outcome="armed"),
                    "seconds_remaining": round(seconds_until_target(now)),
                }
            )
            announced = True
        sleep(min(POLL_SECONDS, seconds_until_target(now)))

    if should_stop():
        return _public_result(state="stopped", outcome="shutdown_before_claim")

    # Once this call begins, termination requests are deliberately not consulted.
    # The durable claim and any broker submission must reach a terminal audit state.
    return attempt_scheduled_cycle(
        now=now,
        settings_factory=settings_factory,
        cycle_runner=cycle_runner,
        store_factory=store_factory,
    )