from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPEC_PATH = PROJECT_ROOT / "research" / "strategy-spec-v1.json"
PINNED_STRATEGY_SPEC_SHA256 = (
    "c98233c83d1b5fc3da3683a9c0c363d82464cc31d92044b963f8f8113d94575a"
)


class StrategySpecError(ValueError):
    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def strategy_spec_digest(spec: dict[str, Any]) -> str:
    """Return the content-addressed identity of a loaded strategy specification."""
    return hashlib.sha256(_canonical_json(spec)).hexdigest()


def load_strategy_spec(path: Path | str = DEFAULT_SPEC_PATH) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StrategySpecError(f"cannot load strategy specification: {source}") from exc
    if not isinstance(value, dict):
        raise StrategySpecError("strategy specification must be a JSON object")
    required = {
        "schema_version",
        "strategy_id",
        "universe",
        "signal",
        "grok_decision",
        "spread",
        "risk",
        "execution",
        "exit",
        "validation",
        "runtime_source_fingerprints",
    }
    missing = sorted(required - value.keys())
    if missing:
        raise StrategySpecError(
            "strategy specification is missing: " + ", ".join(missing)
        )
    if value["schema_version"] != "thesis-strategy-spec-v1":
        raise StrategySpecError("unsupported strategy specification schema")
    universe = value.get("universe")
    if (
        not isinstance(universe, list)
        or not universe
        or any(not isinstance(symbol, str) or not symbol for symbol in universe)
        or len(set(universe)) != len(universe)
    ):
        raise StrategySpecError("strategy universe must contain unique symbols")
    actual_digest = strategy_spec_digest(value)
    if actual_digest != PINNED_STRATEGY_SPEC_SHA256:
        raise StrategySpecError(
            "strategy specification does not match the pre-registered SHA-256: "
            f"expected {PINNED_STRATEGY_SPEC_SHA256}, found {actual_digest}"
        )
    return value


def verify_runtime_fingerprints(
    spec: dict[str, Any],
    *,
    project_root: Path = PROJECT_ROOT,
) -> list[str]:
    """Detect any strategy-code drift from the pre-registered study specification."""
    configured = spec.get("runtime_source_fingerprints")
    if not isinstance(configured, dict) or not configured:
        return ["runtime_source_fingerprints are missing"]
    drift: list[str] = []
    for relative_path, expected in sorted(configured.items()):
        if not isinstance(relative_path, str) or not isinstance(expected, str):
            drift.append("invalid runtime fingerprint entry")
            continue
        source = project_root / relative_path
        try:
            actual = hashlib.sha256(source.read_bytes()).hexdigest()
        except OSError:
            drift.append(f"{relative_path}: source unavailable")
            continue
        if actual != expected:
            drift.append(f"{relative_path}: expected {expected}, found {actual}")
    return drift