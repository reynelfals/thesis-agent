from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thesis.config import Settings


ALPACA_CLI_VERSION = "v0.0.13"


def _public_text(value: Any, *, limit: int = 400) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ")[:limit]


def _response_status(
    parsed: Any,
    stdout: str,
    ok: bool,
    returncode: int,
) -> str:
    if not ok:
        categories = {
            124: "Alpaca CLI timed out",
            126: "Alpaca CLI could not start",
            127: "Alpaca CLI is not installed",
        }
        return categories.get(
            returncode,
            f"Alpaca CLI failed (exit {returncode})",
        )
    if isinstance(parsed, dict):
        if "account_number" in parsed or "options_trading_level" in parsed:
            status = _public_text(parsed.get("status") or "unknown", limit=40)
            level = parsed.get("options_trading_level")
            return f"paper account {status}; options level {level or 'unknown'}"
        if "is_open" in parsed:
            return f"paper market {'open' if parsed.get('is_open') else 'closed'}"
        if parsed.get("id"):
            order_id = _public_text(parsed.get("id"), limit=12)
            order_status = _public_text(parsed.get("status") or "received", limit=40)
            return f"paper order {order_id}… {order_status}"
    return "Alpaca CLI response received" if stdout.strip() else "Alpaca CLI completed"


@dataclass
class CliResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    ok: bool
    parsed: Any = None

    def as_trace(self) -> dict[str, Any]:
        safe_argv = [
            Path(self.argv[0]).name if self.argv else "alpaca",
            *self.argv[1:],
        ]
        return {
            "argv": safe_argv,
            "returncode": self.returncode,
            "ok": self.ok,
            "status": _response_status(
                self.parsed,
                self.stdout,
                self.ok,
                self.returncode,
            ),
        }


def _env(settings: Settings) -> dict[str, str]:
    env = os.environ.copy()
    env["ALPACA_API_KEY"] = settings.api_key
    env["ALPACA_SECRET_KEY"] = settings.secret_key
    env["ALPACA_LIVE_TRADE"] = "false"
    env.pop("ALPACA_LIVE", None)
    return env


def alpaca_bin() -> str | None:
    target = Path(
        os.environ.get("ALPACA_CLI_BIN", "/home/runner/go/bin/alpaca")
    )
    if not target.is_absolute() or not target.is_file() or not os.access(target, os.X_OK):
        return None
    try:
        version = subprocess.run(
            [str(target), "version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if version.returncode != 0 or version.stdout.strip() != ALPACA_CLI_VERSION:
        return None
    return str(target)


def run_alpaca_cli(
    settings: Settings,
    args: list[str],
    *,
    stdin: str | None = None,
    timeout: int = 45,
) -> CliResult:
    binary = alpaca_bin()
    if not binary:
        return CliResult(
            argv=["alpaca", *args],
            returncode=127,
            stdout="",
            stderr="alpaca CLI not on PATH. Install: go install github.com/alpacahq/cli/cmd/alpaca@latest",
            ok=False,
        )
    argv = [binary, *args]
    try:
        proc = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_env(settings),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CliResult(
            argv=argv,
            returncode=124,
            stdout="",
            stderr=f"Alpaca CLI timed out after {timeout} seconds",
            ok=False,
        )
    except OSError as exc:
        return CliResult(
            argv=argv,
            returncode=126,
            stdout="",
            stderr=f"Alpaca CLI could not start: {_public_text(exc)}",
            ok=False,
        )
    parsed = None
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            parsed = None
    return CliResult(
        argv=argv,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        ok=proc.returncode == 0,
        parsed=parsed,
    )
