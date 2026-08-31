from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from pathlib import Path
from typing import Any

from thesis.models import (
    OrderSnapshot,
    PerformanceSnapshot,
    Thesis,
    ThesisStatus,
)


class ThesisStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._init()
        self._import_legacy_cycle()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS theses (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cycles (
                    id TEXT PRIMARY KEY,
                    thesis_id TEXT NOT NULL,
                    json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS order_snapshots (
                    snapshot_key TEXT PRIMARY KEY,
                    thesis_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS performance_snapshots (
                    observed_at TEXT PRIMARY KEY,
                    json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_runs (
                    run_id TEXT PRIMARY KEY,
                    target_at TEXT NOT NULL,
                    claimed_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL,
                    outcome TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS opening_order_reservations (
                    ny_date TEXT PRIMARY KEY,
                    slot TEXT NOT NULL,
                    thesis_id TEXT NOT NULL,
                    client_order_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cycles_created_at ON cycles(created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_orders_thesis ON order_snapshots(thesis_id, observed_at DESC)"
            )

    def upsert(self, thesis: Thesis) -> None:
        payload = thesis.model_dump_json()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO theses (id, status, json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET status=excluded.status, json=excluded.json
                """,
                (
                    thesis.id,
                    thesis.status.value,
                    payload,
                    thesis.created_at.isoformat(),
                ),
            )

    def get(self, thesis_id: str) -> Thesis | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT json FROM theses WHERE id = ?", (thesis_id,)
            ).fetchone()
        if not row:
            return None
        return Thesis.model_validate_json(row["json"])

    def list(self, status: ThesisStatus | None = None) -> list[Thesis]:
        sql = "SELECT json FROM theses"
        args: tuple = ()
        if status is not None:
            sql += " WHERE status = ?"
            args = (status.value,)
        sql += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [Thesis.model_validate_json(r["json"]) for r in rows]

    def open_count(self) -> int:
        return len(self.list(ThesisStatus.OPEN))

    def debit_at_risk(self) -> float:
        total = 0.0
        for t in self.list(ThesisStatus.OPEN):
            if t.structure:
                total += t.structure.max_loss_usd
        return total

    def save_cycle(self, payload: dict[str, Any]) -> None:
        material = json.dumps(payload, indent=2, default=str)
        self._archive_cycle(payload, material.encode("utf-8"))
        self._insert_cycle(payload)
        path = self.path.parent / "last_cycle.json"
        path.write_text(material)

    def _cycle_id(self, payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, default=str)
        return str(
            payload.get("id")
            or sha256(canonical.encode("utf-8")).hexdigest()[:24]
        )

    def _archive_cycle(self, payload: dict[str, Any], content: bytes) -> None:
        cycle_id = self._cycle_id(payload)
        archive = self.path.parent / "cycles"
        archive.mkdir(parents=True, exist_ok=True)
        immutable_path = archive / f"{cycle_id}.json"
        try:
            with immutable_path.open("xb") as handle:
                handle.write(content)
        except FileExistsError:
            try:
                existing_payload = json.loads(immutable_path.read_bytes())
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"immutable cycle archive is unreadable: {cycle_id}"
                ) from exc
            if existing_payload != payload:
                raise ValueError(f"immutable cycle id collision: {cycle_id}")

    def _insert_cycle(self, payload: dict[str, Any]) -> None:
        thesis = payload.get("thesis") or {}
        cycle_id = self._cycle_id(payload)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO cycles (id, thesis_id, json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    cycle_id,
                    str(thesis.get("id") or ""),
                    json.dumps(payload),
                    str(payload.get("at") or ""),
                ),
            )

    def _import_legacy_cycle(self) -> None:
        path = self.path.parent / "last_cycle.json"
        if not path.exists():
            return
        try:
            raw = path.read_bytes()
            payload = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            return
        self._archive_cycle(payload, raw)
        self._insert_cycle(payload)

    def last_cycle(self) -> dict[str, Any] | None:
        path = self.path.parent / "last_cycle.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def list_cycles(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT json FROM cycles ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [json.loads(row["json"]) for row in rows]

    def save_order_snapshot(self, thesis_id: str, snapshot: OrderSnapshot) -> None:
        updated = snapshot.updated_at or snapshot.observed_at
        snapshot_key = "|".join(
            [
                snapshot.order_id,
                snapshot.status,
                str(snapshot.filled_qty),
                updated.isoformat(),
            ]
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO order_snapshots
                    (snapshot_key, thesis_id, order_id, status, observed_at, json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_key,
                    thesis_id,
                    snapshot.order_id,
                    snapshot.status,
                    snapshot.observed_at.isoformat(),
                    snapshot.model_dump_json(),
                ),
            )

    def latest_order_snapshot(self, order_id: str) -> OrderSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT json FROM order_snapshots
                WHERE order_id = ?
                ORDER BY observed_at DESC
                LIMIT 1
                """,
                (order_id,),
            ).fetchone()
        return OrderSnapshot.model_validate_json(row["json"]) if row else None

    def order_snapshots(self, thesis_id: str) -> list[OrderSnapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT json FROM order_snapshots
                WHERE thesis_id = ?
                ORDER BY observed_at ASC
                """,
                (thesis_id,),
            ).fetchall()
        return [OrderSnapshot.model_validate_json(row["json"]) for row in rows]

    def save_performance(self, snapshot: PerformanceSnapshot) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO performance_snapshots (observed_at, json)
                VALUES (?, ?)
                """,
                (snapshot.observed_at.isoformat(), snapshot.model_dump_json()),
            )

    def performance_history(self, limit: int = 500) -> list[PerformanceSnapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT json FROM performance_snapshots
                ORDER BY observed_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            PerformanceSnapshot.model_validate_json(row["json"]) for row in rows
        ]

    def reserve_opening_order(
        self,
        *,
        ny_date: str,
        slot: str,
        thesis_id: str,
        client_order_id: str,
        created_at: str,
    ) -> bool:
        """Atomically claim the only opening-order slot for a New York date."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO opening_order_reservations
                    (ny_date, slot, thesis_id, client_order_id, state, created_at)
                VALUES (?, ?, ?, ?, 'reserved', ?)
                """,
                (ny_date, slot, thesis_id, client_order_id, created_at),
            )
        return cursor.rowcount == 1

    def mark_opening_order_reservation(
        self,
        *,
        client_order_id: str,
        state: str,
    ) -> None:
        if state not in {"reserved", "dispatch_ambiguous", "submitted"}:
            raise ValueError("invalid opening-order reservation state")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE opening_order_reservations
                SET state = ?
                WHERE client_order_id = ?
                """,
                (state, client_order_id),
            )

    def opening_order_reservation(self, ny_date: str) -> dict[str, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT ny_date, slot, thesis_id, client_order_id, state, created_at
                FROM opening_order_reservations
                WHERE ny_date = ?
                """,
                (ny_date,),
            ).fetchone()
        return dict(row) if row else None

    def claim_scheduled_run(
        self,
        *,
        run_id: str,
        target_at: str,
        claimed_at: str,
    ) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO scheduled_runs
                    (run_id, target_at, claimed_at, state)
                VALUES (?, ?, ?, 'claimed')
                """,
                (run_id, target_at, claimed_at),
            )
        return cursor.rowcount == 1

    def finish_scheduled_run(
        self,
        *,
        run_id: str,
        state: str,
        outcome: str,
        finished_at: str,
    ) -> None:
        if state not in {"completed", "failed", "skipped"}:
            raise ValueError("scheduled run state must be terminal")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE scheduled_runs
                SET state = ?, outcome = ?, finished_at = ?
                WHERE run_id = ? AND state = 'claimed'
                """,
                (state, outcome[:80], finished_at, run_id),
            )

    def scheduled_run(self, run_id: str) -> dict[str, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_id, target_at, claimed_at, finished_at, state, outcome
                FROM scheduled_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        return dict(row) if row else None
