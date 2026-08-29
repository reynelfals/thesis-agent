from __future__ import annotations

import pytest

from thesis.alpaca.client import PaperClient


class FakeTrading:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    def get(self, path, params):
        self.calls.append((path, params))
        return next(self.pages)


def _fill(number: int) -> dict:
    return {
        "id": f"fill-{number}",
        "transaction_time": f"2026-01-01T10:{number % 60:02d}:00Z",
        "order_id": f"order-{number}",
        "symbol": "SPY260918C00600000",
        "side": "buy",
        "qty": "1",
        "price": "1",
    }


def test_fill_activities_reads_every_page() -> None:
    client = object.__new__(PaperClient)
    client.trading = FakeTrading(
        [[_fill(number) for number in range(100)], [_fill(100)]]
    )

    fills = client.fill_activities()

    assert len(fills) == 101
    assert client.trading.calls[1][1]["page_token"] == "fill-99"


def test_fill_activities_refuses_partial_history() -> None:
    client = object.__new__(PaperClient)
    client.trading = FakeTrading([[_fill(number) for number in range(100)]])

    with pytest.raises(RuntimeError, match="partial data"):
        client.fill_activities(max_pages=1)