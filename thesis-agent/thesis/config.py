from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PAPER_BASE = "https://paper-api.alpaca.markets"
LIVE_BASE = "https://api.alpaca.markets"

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"missing {name} in environment / .env")
    return value


class Settings:
    api_key: str
    secret_key: str
    base_url: str
    xai_api_key: str
    grok_model: str
    db_path: Path
    allow_execute: bool
    demo_starting_equity: float

    def __init__(self) -> None:
        self.api_key = _require("APCA_API_KEY_ID")
        self.secret_key = _require("APCA_API_SECRET_KEY")
        raw = os.getenv("APCA_API_BASE_URL", PAPER_BASE).strip().rstrip("/")
        if raw.endswith("/v2"):
            raw = raw[: -len("/v2")]
        self.base_url = raw
        self.xai_api_key = os.getenv("XAI_API_KEY", "").strip()
        self.grok_model = os.getenv("GROK_MODEL", "grok-4.6").strip() or "grok-4.6"
        self.db_path = Path(os.getenv("THESIS_DB", ROOT / "data" / "thesis.sqlite"))
        self.allow_execute = os.getenv("THESIS_ALLOW_EXECUTE", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        try:
            self.demo_starting_equity = float(
                os.getenv("THESIS_DEMO_STARTING_EQUITY", "100000")
            )
        except ValueError as exc:
            raise ConfigError("THESIS_DEMO_STARTING_EQUITY must be a number") from exc

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
