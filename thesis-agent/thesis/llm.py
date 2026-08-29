from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from thesis.config import ConfigError, Settings
from thesis.models import Side, Thesis
from thesis.observe import MarketSnapshot
from thesis.risk import ALLOWLIST, MIN_CONVICTION


SYSTEM = """You write testable directional options theses for liquid US names.
You do NOT place orders. You do NOT invent fills or prices.
Return a single JSON object with keys:
  underlying (one of the allowlist),
  side (bullish or bearish),
  regime,
  setup (2-4 sentences: why this directional debit spread, not a naked option),
  invalidation (precise: price level or regime change that kills the thesis),
  horizon (e.g. 14-45 DTE),
  expected_move_pct (number),
  iv_note (short; we do not have IV rank — say so if unknown),
  conviction (0 to 1).
If nothing is worth trading, set conviction to 0 and explain in setup.
Prefer defined-risk debit verticals. No 0DTE. No naked short. No crypto.
"""


def grok_client(settings: Settings) -> OpenAI:
    if not settings.xai_api_key:
        raise ConfigError("missing XAI_API_KEY")
    return OpenAI(api_key=settings.xai_api_key, base_url="https://api.x.ai/v1")


def draft_thesis(settings: Settings, snaps: list[MarketSnapshot]) -> Thesis:
    payload = [
        {
            "symbol": s.symbol,
            "spot": round(s.spot, 2),
            "sma5": round(s.sma5, 2),
            "sma20": round(s.sma20, 2),
            "ret_5d_pct": round(s.ret_5d_pct, 2),
            "realized_vol_20d": round(s.realized_vol_20d, 3),
            "regime": s.regime,
        }
        for s in snaps
    ]
    user = (
        "Allowlist: "
        + ", ".join(ALLOWLIST)
        + "\nMarket snapshots:\n"
        + json.dumps(payload, indent=2)
        + "\nWrite one thesis on the single best name, or conviction 0."
    )
    client = grok_client(settings)
    resp = client.chat.completions.create(
        model=settings.grok_model,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user},
        ],
    )
    text = resp.choices[0].message.content or "{}"
    data: dict[str, Any] = json.loads(text)
    underlying = str(data.get("underlying", "")).upper()
    if underlying not in ALLOWLIST:
        return Thesis(
            underlying="SPY",
            side=Side.BULLISH,
            regime="invalid_model_output",
            setup="Model returned an underlying outside the allowlist. No trade.",
            invalidation="n/a",
            horizon="n/a",
            expected_move_pct=0,
            iv_note="n/a",
            conviction=0.0,
            notes="rejected invalid underlying",
            decision="no_trade",
        )
    side_raw = str(data.get("side", "bullish")).lower()
    side = Side.BEARISH if "bear" in side_raw else Side.BULLISH
    conviction = float(data.get("conviction", 0))
    conviction = max(0.0, min(1.0, conviction))
    return Thesis(
        underlying=underlying,
        side=side,
        regime=str(data.get("regime", snaps[0].regime if snaps else "unknown")),
        setup=str(data.get("setup", "")),
        invalidation=str(data.get("invalidation", "")),
        horizon=str(data.get("horizon", "14-45 DTE")),
        expected_move_pct=float(data.get("expected_move_pct", 0) or 0),
        iv_note=str(data.get("iv_note", "IV rank not supplied")),
        conviction=conviction,
        notes=f"model={settings.grok_model} skip={conviction < MIN_CONVICTION}",
    )
