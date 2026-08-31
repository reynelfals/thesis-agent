from __future__ import annotations

from thesis.research.alpaca_probe import probe_historical_capabilities
from thesis.research.report import capability_report
from thesis.research.spec import load_strategy_spec


def test_capability_probe_fails_closed_without_historical_nbbo() -> None:
    calls: list[tuple[str, str, dict[str, str]]] = []

    def requester(base: str, path: str, params: dict[str, str]):
        calls.append((base, path, params))
        if path == "/v2/stocks/SPY/bars":
            return 200, {"bars": [{"t": "2025-04-01T20:00:00Z"}]}
        if path == "/v2/options/contracts":
            return 200, {
                "option_contracts": [
                    {
                        "symbol": "SPY250404C00530000",
                        "strike_price": "530",
                        "type": "call",
                    }
                ]
            }
        if path.endswith("/bars"):
            return 200, {"bars": {"SPY250404C00530000": [{"c": 1.0}]}}
        if path.endswith("/trades"):
            return 200, {"trades": {"SPY250404C00530000": [{"p": 1.0}]}}
        if path.endswith("/quotes"):
            return 404, {"message": "Not Found"}
        raise AssertionError(path)

    probe = probe_historical_capabilities(requester)
    report = capability_report(probe, load_strategy_spec())

    assert all(call[1] != "/v2/orders" for call in calls)
    assert probe["order_endpoints_called"] == 0
    assert probe["capabilities"]["historical_option_bars"] is True
    assert probe["capabilities"]["historical_option_trades"] is True
    assert probe["capabilities"]["historical_option_nbbo_quotes"] is False
    assert probe["scientific_dataset_ready"] is False
    assert report["status"] == "not_validated"
    assert report["performance"]["expectancy_dollars"] is None