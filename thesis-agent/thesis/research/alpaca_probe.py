from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from thesis.config import ConfigError, alpaca_credential_names

Requester = Callable[
    [str, str, dict[str, str]],
    tuple[int, dict[str, Any]],
]

TRADING_BASE = "https://paper-api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"


class ProbeError(RuntimeError):
    pass


def _credentials() -> tuple[str, str]:
    try:
        key_name, secret_name = alpaca_credential_names()
        key = os.getenv(key_name, "").strip()
        secret = os.getenv(secret_name, "").strip()
    except ConfigError as exc:
        raise ProbeError(str(exc)) from exc
    missing = [
        name
        for name, value in ((key_name, key), (secret_name, secret))
        if not value
    ]
    if missing:
        raise ProbeError(f"missing {', '.join(missing)} in environment / .env")
    return key, secret


def _requester(
    base: str,
    path: str,
    params: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    key, secret = _credentials()
    request = Request(
        base + path + "?" + urlencode(params),
        headers={
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=45) as response:
            value = json.load(response)
            return response.status, value if isinstance(value, dict) else {}
    except HTTPError as exc:
        try:
            body = json.loads(exc.read(2000).decode("utf-8", "replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        message = body.get("message") if isinstance(body, dict) else None
        return exc.code, {"message": str(message or "request failed")}
    except URLError as exc:
        raise ProbeError("Alpaca data service is unreachable") from exc


def _count_rows(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return sum(len(rows) for rows in value.values() if isinstance(rows, list))
    return 0


def _evidence(
    status: int,
    payload: dict[str, Any],
    key: str,
) -> dict[str, Any]:
    rows = payload.get(key)
    return {
        "http_status": status,
        "rows": _count_rows(rows),
        "paginated": bool(payload.get("next_page_token")),
        "available": status == 200 and _count_rows(rows) > 0,
        "message": (
            str(payload.get("message") or "")
            if status != 200
            else ""
        ),
    }


def probe_historical_capabilities(
    requester: Requester = _requester,
) -> dict[str, Any]:
    """Probe only documented GET endpoints; this function cannot submit orders."""
    stock_status, stock_payload = requester(
        DATA_BASE,
        "/v2/stocks/SPY/bars",
        {
            "timeframe": "1Day",
            "start": "2025-04-01T00:00:00Z",
            "end": "2025-04-08T00:00:00Z",
            "feed": "iex",
            "limit": "10",
        },
    )
    contract_status, contract_payload = requester(
        TRADING_BASE,
        "/v2/options/contracts",
        {
            "underlying_symbols": "SPY",
            "status": "inactive",
            "expiration_date": "2025-04-04",
            "strike_price_gte": "500",
            "strike_price_lte": "560",
            "limit": "1000",
        },
    )
    contracts = (
        contract_payload.get("option_contracts", [])
        if contract_status == 200
        else []
    )
    contracts = [
        contract
        for contract in contracts
        if isinstance(contract, dict) and isinstance(contract.get("symbol"), str)
    ]
    contracts.sort(
        key=lambda contract: (
            abs(float(contract.get("strike_price") or 0) - 530),
            str(contract.get("type") or ""),
        )
    )
    symbols = ",".join(
        str(contract["symbol"]) for contract in contracts[:10]
    )
    option_params = {
        "symbols": symbols,
        "start": "2025-04-03T13:30:00Z",
        "end": "2025-04-03T14:00:00Z",
        "limit": "100",
    }
    if symbols:
        bars_status, bars_payload = requester(
            DATA_BASE,
            "/v1beta1/options/bars",
            {**option_params, "timeframe": "1Min"},
        )
        trades_status, trades_payload = requester(
            DATA_BASE,
            "/v1beta1/options/trades",
            option_params,
        )
        quotes_status, quotes_payload = requester(
            DATA_BASE,
            "/v1beta1/options/quotes",
            option_params,
        )
    else:
        bars_status, bars_payload = 0, {"message": "no expired contracts"}
        trades_status, trades_payload = 0, {"message": "no expired contracts"}
        quotes_status, quotes_payload = 0, {"message": "no expired contracts"}
    evidence = {
        "stock_bars": _evidence(stock_status, stock_payload, "bars"),
        "expired_option_contracts": {
            "http_status": contract_status,
            "rows": len(contracts),
            "paginated": bool(contract_payload.get("next_page_token")),
            "available": contract_status == 200 and bool(contracts),
            "message": (
                str(contract_payload.get("message") or "")
                if contract_status != 200
                else ""
            ),
        },
        "historical_option_bars": _evidence(
            bars_status,
            bars_payload,
            "bars",
        ),
        "historical_option_trades": _evidence(
            trades_status,
            trades_payload,
            "trades",
        ),
        "historical_option_nbbo_quotes": _evidence(
            quotes_status,
            quotes_payload,
            "quotes",
        ),
    }
    capabilities = {
        name: bool(item["available"]) for name, item in evidence.items()
    }
    blockers = []
    if not capabilities["historical_option_nbbo_quotes"]:
        blockers.append(
            "Alpaca exposes historical option bars and trades but the historical "
            "option quotes endpoint is unavailable; neither bars nor last trades "
            "are acceptable substitutes for contemporaneous NBBO fills."
        )
    blockers.append(
        "The deployed Grok strategy has no historical archive of contemporaneously "
        "recorded decisions and therefore cannot be reconstructed without hindsight."
    )
    blockers.append(
        "The deployed strategy has no executable exit policy; the frozen study exit "
        "rules are prospective and cannot be represented as historical production behavior."
    )
    return {
        "probe_version": "alpaca-historical-capability-v1",
        "read_only": True,
        "order_endpoints_called": 0,
        "data_availability_start": "2024-02",
        "evidence": evidence,
        "capabilities": capabilities,
        "scientific_dataset_ready": not blockers,
        "blocking_reasons": blockers,
        "official_sources": [
            "https://docs.alpaca.markets/us/docs/historical-option-data",
            "https://docs.alpaca.markets/us/reference/optionbars",
            "https://docs.alpaca.markets/us/reference/optiontrades",
            "https://docs.alpaca.markets/us/reference/get-options-contracts",
        ],
    }