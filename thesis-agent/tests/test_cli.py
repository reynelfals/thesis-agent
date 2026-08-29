from __future__ import annotations

import subprocess
from pathlib import Path

from thesis.tools.cli import CliResult, alpaca_bin, run_alpaca_cli


class Settings:
    api_key = "paper-key"
    secret_key = "paper-secret"


def test_trace_summarizes_account_without_raw_output_or_binary_path() -> None:
    result = CliResult(
        argv=["/home/runner/go/bin/alpaca", "account", "get", "--quiet"],
        returncode=0,
        stdout='{"account_number":"123456789","status":"ACTIVE"}',
        stderr="",
        ok=True,
        parsed={
            "account_number": "123456789",
            "status": "ACTIVE",
            "options_trading_level": 3,
        },
    )

    trace = result.as_trace()

    assert trace["argv"][0] == "alpaca"
    assert trace["status"] == "paper account ACTIVE; options level 3"
    assert "stdout" not in trace
    assert "123456789" not in str(trace)
    assert "paper-secret" not in str(trace)


def test_cli_timeout_returns_explicit_failure(monkeypatch) -> None:
    monkeypatch.setattr("thesis.tools.cli.alpaca_bin", lambda: "/bin/alpaca")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)

    result = run_alpaca_cli(Settings(), ["clock", "--quiet"], timeout=2)

    assert result.returncode == 124
    assert result.ok is False
    assert result.as_trace()["status"] == "Alpaca CLI timed out"


def test_cli_error_trace_never_persists_raw_error_text() -> None:
    result = CliResult(
        argv=["alpaca", "clock"],
        returncode=1,
        stdout="",
        stderr=(
            'Authorization: Bearer top-secret '
            '{"APCA_API_SECRET_KEY":"another-secret","account_number":"123456789"}'
        ),
        ok=False,
    )

    trace = result.as_trace()
    serialized = str(trace)

    assert "top-secret" not in serialized
    assert "another-secret" not in serialized
    assert "123456789" not in serialized
    assert "stderr" not in trace
    assert trace["status"] == "Alpaca CLI failed (exit 1)"


def test_cli_resolution_uses_validated_absolute_target(
    monkeypatch,
    tmp_path,
) -> None:
    validated = tmp_path / "validated-alpaca"
    validated.write_text("#!/bin/sh\necho v0.0.13\n")
    validated.chmod(0o755)
    poison_dir = tmp_path / "poison"
    poison_dir.mkdir()
    poison = poison_dir / "alpaca"
    poison.write_text("#!/bin/sh\n")
    poison.chmod(0o755)
    monkeypatch.setenv("ALPACA_CLI_BIN", str(validated))
    monkeypatch.setenv("PATH", f"{poison_dir}:/usr/bin")

    assert alpaca_bin() == str(validated)


def test_cli_resolution_rejects_relative_or_wrong_version(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    relative = Path("relative-alpaca")
    relative.write_text("#!/bin/sh\necho v0.0.13\n")
    relative.chmod(0o755)
    monkeypatch.setenv("ALPACA_CLI_BIN", str(relative))
    assert alpaca_bin() is None

    wrong = tmp_path / "wrong-alpaca"
    wrong.write_text("#!/bin/sh\necho v0.0.12\n")
    wrong.chmod(0o755)
    monkeypatch.setenv("ALPACA_CLI_BIN", str(wrong))
    assert alpaca_bin() is None