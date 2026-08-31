"""Durable, market-hours scheduling for execution-disabled analysis cycles."""

from __future__ import annotations

import time
import multiprocessing
import os
import signal
from datetime import date, datetime, time as wall_time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from thesis.alpaca.client import PaperClient
from thesis.config import Settings, load_settings
from thesis.cycle import CycleResult, run_cycle
from thesis.store import ThesisStore

EASTERN = ZoneInfo("America/New_York")
FIRST_SLOT = wall_time(9, 35)
LAST_SLOT = wall_time(15, 35)
SLOT_INTERVAL = timedelta(minutes=30)
SLOT_GRACE = timedelta(minutes=15)
POLL_SECONDS = 30.0
SLOT_RUN_TIMEOUT_SECONDS = 14 * 60.0
RUN_ID_PREFIX = "thesis-analysis"

SettingsFactory = Callable[[], Settings]
CycleRunner = Callable[..., CycleResult]
StoreFactory = Callable[[Path], ThesisStore]
Emitter = Callable[[dict[str, Any]], None]
StopChecker = Callable[[], bool]
MarketOpenReader = Callable[[Settings], bool]
CycleExecutor = Callable[[CycleRunner, Settings, float], dict[str, str]]


def _eastern(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("recurring scheduler clock must be timezone-aware")
    return now.astimezone(EASTERN)


def _slot_on(day: date, value: wall_time) -> datetime:
    return datetime.combine(day, value, tzinfo=EASTERN)


def eligible_slot(now: datetime) -> datetime | None:
    """Return only the currently eligible slot; never return an old backfill."""
    local = _eastern(now)
    if local.weekday() >= 5:
        return None

    first = _slot_on(local.date(), FIRST_SLOT)
    last = _slot_on(local.date(), LAST_SLOT)
    if local < first:
        return None

    intervals = int((local - first) // SLOT_INTERVAL)
    target = first + intervals * SLOT_INTERVAL
    if target > last or local > target + SLOT_GRACE:
        return None
    return target


def slot_run_id(target: datetime) -> str:
    local = _eastern(target)
    return f"{RUN_ID_PREFIX}-{local:%Y-%m-%d-%H%M}-et"


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _result(
    *,
    state: str,
    outcome: str,
    target: datetime | None = None,
    decision: str = "",
) -> dict[str, Any]:
    return {
        "event": "recurring_market_analysis",
        "run_id": slot_run_id(target) if target is not None else "",
        "state": state,
        "outcome": outcome,
        "decision": decision,
        "order_submitted": False,
        "target_at": target.isoformat() if target is not None else "",
        "execute": False,
    }


def _preflight(settings: Settings) -> None:
    settings.assert_paper()
    if settings.allow_execute:
        raise ValueError(
            "recurring analysis worker refuses THESIS_ALLOW_EXECUTE-enabled settings"
        )


def paper_market_is_open(settings: Settings) -> bool:
    """Read the authoritative paper-account market clock without any write."""
    return bool(PaperClient(settings).clock().is_open)


def _invoke_cycle(cycle_runner: CycleRunner, settings: Settings) -> dict[str, str]:
    try:
        cycle = cycle_runner(settings, execute=False)
        decision = str(cycle.decision or "")
        order_id = str(getattr(cycle.thesis, "order_id", "") or "")
        if order_id:
            return {
                "state": "failed",
                "outcome": "analysis_returned_order",
                "decision": decision,
            }
        return {
            "state": "completed",
            "outcome": decision or "unknown",
            "decision": decision,
        }
    except BaseException:
        return {"state": "failed", "outcome": "cycle_failed", "decision": ""}


def run_cycle_direct(
    cycle_runner: CycleRunner,
    settings: Settings,
    _timeout_seconds: float,
) -> dict[str, str]:
    """Synchronous executor retained for deterministic injected-runner tests."""
    return _invoke_cycle(cycle_runner, settings)


def _isolated_target(
    connection: Any,
    cycle_runner: CycleRunner,
    settings: Settings,
) -> None:
    try:
        if hasattr(os, "setsid"):
            os.setsid()
        connection.send(_invoke_cycle(cycle_runner, settings))
    finally:
        connection.close()


def _stop_isolated(process: multiprocessing.Process) -> None:
    if not process.is_alive():
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (AttributeError, ProcessLookupError, PermissionError):
        process.terminate()
    process.join(timeout=2.0)
    if process.is_alive():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (AttributeError, ProcessLookupError, PermissionError):
            process.kill()
        process.join()


def run_cycle_isolated(
    cycle_runner: CycleRunner,
    settings: Settings,
    timeout_seconds: float,
) -> dict[str, str]:
    """Run one analysis in a disposable process and enforce its deadline."""
    try:
        context = multiprocessing.get_context("fork")
    except ValueError:  # pragma: no cover - production is Linux
        context = multiprocessing.get_context()
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_isolated_target,
        args=(sender, cycle_runner, settings),
        daemon=False,
    )
    process.start()
    sender.close()
    try:
        if receiver.poll(timeout_seconds):
            try:
                payload = receiver.recv()
            except EOFError:
                payload = {
                    "state": "failed",
                    "outcome": "cycle_process_failed",
                    "decision": "",
                }
            process.join(timeout=2.0)
            _stop_isolated(process)
            return payload
        _stop_isolated(process)
        return {"state": "failed", "outcome": "cycle_timeout", "decision": ""}
    finally:
        receiver.close()


def attempt_current_slot(
    *,
    now: datetime,
    settings_factory: SettingsFactory = load_settings,
    cycle_runner: CycleRunner = run_cycle,
    store_factory: StoreFactory = ThesisStore,
    market_open_reader: MarketOpenReader = paper_market_is_open,
    cycle_executor: CycleExecutor = run_cycle_direct,
    timeout_seconds: float = SLOT_RUN_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    local_now = _eastern(now)
    settings = settings_factory()
    try:
        _preflight(settings)
    except Exception:
        return _result(state="not_armed", outcome="unsafe_configuration")

    target = eligible_slot(local_now)
    if target is None:
        return _result(state="idle", outcome="no_current_slot")

    run_id = slot_run_id(target)
    store = store_factory(settings.db_path)
    claimed = store.claim_scheduled_run(
        run_id=run_id,
        target_at=target.isoformat(),
        claimed_at=_timestamp(local_now),
    )
    if not claimed:
        existing = store.scheduled_run(run_id)
        return _result(
            state="already_claimed",
            outcome=(existing or {}).get("state", "claimed"),
            target=target,
        )

    try:
        market_open = bool(market_open_reader(settings))
    except BaseException:
        state, outcome, decision = "failed", "market_clock_failed", ""
    else:
        if not market_open:
            state, outcome, decision = "skipped", "market_closed", ""
        else:
            try:
                bounded_timeout = min(
                    max(float(timeout_seconds), 0.001),
                    SLOT_RUN_TIMEOUT_SECONDS,
                )
                payload = cycle_executor(cycle_runner, settings, bounded_timeout)
                state = payload["state"]
                outcome = payload["outcome"]
                decision = payload.get("decision", "")
            except BaseException:
                state, outcome, decision = "failed", "cycle_process_failed", ""

    store.finish_scheduled_run(
        run_id=run_id,
        state=state,
        outcome=outcome,
        finished_at=_timestamp(datetime.now(timezone.utc)),
    )
    return _result(
        state=state,
        outcome=outcome,
        target=target,
        decision=decision,
    )


def run_worker(
    *,
    now_factory: Callable[[], datetime] = lambda: datetime.now(EASTERN),
    sleep: Callable[[float], Any] = time.sleep,
    should_stop: StopChecker = lambda: False,
    settings_factory: SettingsFactory = load_settings,
    cycle_runner: CycleRunner = run_cycle,
    store_factory: StoreFactory = ThesisStore,
    market_open_reader: MarketOpenReader = paper_market_is_open,
    cycle_executor: CycleExecutor = run_cycle_isolated,
    timeout_seconds: float = SLOT_RUN_TIMEOUT_SECONDS,
    emit: Emitter | None = None,
) -> dict[str, Any]:
    try:
        _preflight(settings_factory())
    except Exception:
        return _result(state="not_armed", outcome="unsafe_configuration")

    last_emitted: tuple[str, str] | None = None
    while not should_stop():
        result = attempt_current_slot(
            now=now_factory(),
            settings_factory=settings_factory,
            cycle_runner=cycle_runner,
            store_factory=store_factory,
            market_open_reader=market_open_reader,
            cycle_executor=cycle_executor,
            timeout_seconds=timeout_seconds,
        )
        marker = (result["run_id"], result["state"])
        if emit is not None and marker != last_emitted:
            emit(result)
            last_emitted = marker
        if result["state"] == "not_armed":
            return result
        sleep(POLL_SECONDS)

    return _result(state="stopped", outcome="shutdown_requested")