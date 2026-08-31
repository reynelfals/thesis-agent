from __future__ import annotations

import math
import os
from pathlib import Path

from dotenv import load_dotenv

PAPER_BASE = "https://paper-api.alpaca.markets"
LIVE_BASE = "https://api.alpaca.markets"
ACCOUNT_PROFILES = {
    "development": (
        "DEV_APCA_API_KEY_ID",
        "DEV_APCA_API_SECRET_KEY",
        "Development paper account",
    ),
    "judge": (
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
        "Judge paper account",
    ),
}
DEFAULT_MIN_AVG_DOLLAR_VOLUME = 50_000_000.0
DEFAULT_MAX_OPTION_BID_ASK_PCT = 25.0
DEFAULT_SCOUT_UNIVERSE = "expanded"

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"missing {name} in environment / .env")
    return value


def account_profile() -> str:
    value = _require("THESIS_ACCOUNT_PROFILE").lower()
    if value not in ACCOUNT_PROFILES:
        choices = ", ".join(ACCOUNT_PROFILES)
        raise ConfigError(f"THESIS_ACCOUNT_PROFILE must be one of: {choices}")
    return value


def alpaca_credential_names(profile: str | None = None) -> tuple[str, str]:
    selected = profile or account_profile()
    try:
        key_name, secret_name, _ = ACCOUNT_PROFILES[selected]
    except KeyError as exc:
        choices = ", ".join(ACCOUNT_PROFILES)
        raise ConfigError(
            f"THESIS_ACCOUNT_PROFILE must be one of: {choices}"
        ) from exc
    return key_name, secret_name


def _number(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float | None = None,
) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if not math.isfinite(value) or value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name} must be at most {maximum}")
    return value


def _choice(name: str, default: str, allowed: set[str]) -> str:
    value = os.getenv(name, default).strip().lower()
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ConfigError(f"{name} must be one of: {choices}")
    return value


class Settings:
    account_profile: str
    account_profile_label: str
    api_key: str
    secret_key: str
    base_url: str
    xai_api_key: str
    grok_model: str
    db_path: Path
    allow_execute: bool
    demo_starting_equity: float
    min_avg_dollar_volume: float
    max_option_bid_ask_pct: float
    scout_universe: str

    def __init__(self) -> None:
        self.account_profile = account_profile()
        key_name, secret_name = alpaca_credential_names(self.account_profile)
        self.api_key = _require(key_name)
        self.secret_key = _require(secret_name)
        self.account_profile_label = ACCOUNT_PROFILES[self.account_profile][2]
        raw = os.getenv("APCA_API_BASE_URL", PAPER_BASE).strip().rstrip("/")
        if raw.endswith("/v2"):
            raw = raw[: -len("/v2")]
        self.base_url = raw
        self.xai_api_key = os.getenv("XAI_API_KEY", "").strip()
        self.grok_model = os.getenv("GROK_MODEL", "grok-4.6").strip() or "grok-4.6"
        allowed_db = (
            ROOT / "data" / f"{self.account_profile}-thesis.sqlite"
        ).resolve()
        configured_db = os.getenv("THESIS_DB")
        if configured_db is None:
            resolved_db = allowed_db
        else:
            candidate = Path(configured_db).expanduser()
            if not candidate.is_absolute():
                candidate = ROOT / candidate
            resolved_db = candidate.resolve()
            if resolved_db != allowed_db:
                raise ConfigError(
                    "THESIS_DB must resolve to "
                    f"{allowed_db} for the {self.account_profile} profile"
                )
        self.db_path = resolved_db
        self.allow_execute = os.getenv("THESIS_ALLOW_EXECUTE", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        self.demo_starting_equity = _number(
            "THESIS_DEMO_STARTING_EQUITY",
            100_000.0,
            minimum=0.0,
        )
        self.min_avg_dollar_volume = _number(
            "THESIS_MIN_AVG_DOLLAR_VOLUME",
            DEFAULT_MIN_AVG_DOLLAR_VOLUME,
            minimum=0.0,
        )
        self.max_option_bid_ask_pct = _number(
            "THESIS_MAX_OPTION_BID_ASK_PCT",
            DEFAULT_MAX_OPTION_BID_ASK_PCT,
            minimum=0.0,
            maximum=200.0,
        )
        self.scout_universe = _choice(
            "THESIS_SCOUT_UNIVERSE",
            DEFAULT_SCOUT_UNIVERSE,
            {"baseline", "expanded"},
        )

    def assert_paper(self) -> None:
        if self.base_url != PAPER_BASE:
            raise ConfigError(
                f"refusing to start: base URL must be {PAPER_BASE}, got {self.base_url}"
            )
        if self.base_url == LIVE_BASE:
            raise ConfigError("refusing live trading")


def load_settings() -> Settings:
    settings = Settings()
    settings.assert_paper()
    return settings
