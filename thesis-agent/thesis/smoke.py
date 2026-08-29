from __future__ import annotations

import json
import sys

from alpaca.trading.enums import ContractType

from thesis.config import load_settings
from thesis.alpaca.client import PaperClient
from thesis.risk import ALLOWLIST


def main() -> int:
    settings = load_settings()
    client = PaperClient(settings)
    account = client.account()
    clock = client.clock()
    spy = client.last_price("SPY")
    chain = client.option_chain("SPY", option_type=ContractType.CALL)
    n = len(chain.option_contracts or [])
    print(
        json.dumps(
            {
                "paper": True,
                "base_url": settings.base_url,
                "account_number": account.account_number,
                "status": str(account.status),
                "options_trading_level": account.options_trading_level,
                "equity": str(account.equity),
                "options_buying_power": str(account.options_buying_power),
                "market_open": bool(clock.is_open),
                "spy": spy,
                "spy_call_contracts": n,
                "allowlist": list(ALLOWLIST),
            },
            indent=2,
        )
    )
    if int(account.options_trading_level or 0) < 3:
        print("warning: options level < 3", file=sys.stderr)
        return 1
    if n == 0:
        print("warning: empty SPY call chain", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
