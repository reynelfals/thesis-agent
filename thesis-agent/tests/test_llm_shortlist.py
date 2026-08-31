from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import thesis.llm as llm_module
from thesis.observe import MarketSnapshot
from thesis.tools.mcp import McpToolResult


def _snap() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="SPY",
        spot=600,
        sma5=595,
        sma20=590,
        ret_5d_pct=1,
        realized_vol_20d=0.2,
        regime="uptrend/normal_vol",
    )


def _call(name: str, arguments: dict, call_id: str = "call-1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def _response(*calls):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=None, tool_calls=list(calls))
            )
        ]
    )


def _trade_request(**overrides):
    payload = {
        "underlying": "SPY",
        "side": "bullish",
        "regime": "uptrend",
        "setup": "Liquid trend continuation suited to a defined-risk debit spread.",
        "invalidation": "Close below the 20-day moving average.",
        "horizon": "14-45 DTE",
        "expected_move_pct": 2.0,
        "iv_note": "IV rank was not supplied.",
        "conviction": 0.8,
    }
    payload.update(overrides)
    return payload


class FakeMcp:
    def __init__(self) -> None:
        self.calls = []

    def model_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_stock_snapshot",
                    "description": "read-only",
                    "parameters": {"type": "object"},
                },
            }
        ]

    async def call_model_tool(
        self,
        name,
        arguments,
        *,
        allowed_symbols,
        feasible_sides,
    ):
        self.calls.append((name, arguments, allowed_symbols, feasible_sides))
        return McpToolResult(
            name=name,
            ok=True,
            data={"SPY": {"latestTrade": {"p": 600}}},
            status="MCP research returned one symbol",
        )


def _run_agent(monkeypatch, responses):
    captured = []
    iterator = iter(responses)

    def create(**kwargs):
        captured.append(kwargs)
        return next(iterator)

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(llm_module, "grok_client", lambda settings: client)
    mcp = FakeMcp()
    draft = asyncio.run(
        llm_module.draft_thesis(
            SimpleNamespace(grok_model="grok-test"),
            [_snap()],
            {"SPY": ["bullish"]},
            mcp,
        )
    )
    return draft, mcp, captured


def test_agent_researches_with_mcp_then_requests_trade(monkeypatch) -> None:
    research = _call("get_stock_snapshot", {"symbols": "SPY"}, "research")
    request = _call(
        llm_module.REQUEST_TRADE_TOOL,
        _trade_request(),
        "request",
    )

    draft, mcp, captured = _run_agent(
        monkeypatch,
        [_response(research), _response(request)],
    )

    assert draft.thesis.underlying == "SPY"
    assert draft.thesis.conviction == 0.8
    assert draft.research_calls == 1
    assert mcp.calls[0][0] == "get_stock_snapshot"
    exposed = {
        tool["function"]["name"] for tool in captured[0]["tools"]
    }
    assert exposed == {"get_stock_snapshot", llm_module.REQUEST_TRADE_TOOL}
    assert not exposed & {
        "place_option_order",
        "cancel_all_orders",
        "replace_order_by_id",
        "exercise_options_position",
    }


def test_agent_underlying_and_side_remain_deterministically_constrained(
    monkeypatch,
) -> None:
    research = _call("get_stock_snapshot", {"symbols": "SPY"}, "research")
    request = _call(
        llm_module.REQUEST_TRADE_TOOL,
        _trade_request(underlying="AAPL", side="bearish"),
        "request",
    )

    draft, _, _ = _run_agent(
        monkeypatch,
        [_response(research), _response(request)],
    )

    assert draft.thesis.underlying == "SPY"
    assert draft.thesis.conviction == 0
    assert draft.thesis.decision == "no_trade"
    assert "shortlist" in draft.thesis.setup


def test_agent_low_conviction_is_rejected_not_clamped(monkeypatch) -> None:
    research = _call("get_stock_snapshot", {"symbols": "SPY"}, "research")
    request = _call(
        llm_module.REQUEST_TRADE_TOOL,
        _trade_request(conviction=0.1),
        "request",
    )

    draft, _, _ = _run_agent(
        monkeypatch,
        [_response(research), _response(request)],
    )

    assert draft.thesis.conviction == 0
    assert draft.thesis.decision == "no_trade"
    assert "low conviction" in draft.thesis.notes


def test_agent_nonfinite_numeric_response_is_rejected(monkeypatch) -> None:
    research = _call("get_stock_snapshot", {"symbols": "SPY"}, "research")
    request = _call(
        llm_module.REQUEST_TRADE_TOOL,
        _trade_request(conviction=float("nan")),
        "request",
    )

    draft, _, _ = _run_agent(
        monkeypatch,
        [_response(research), _response(request)],
    )

    assert draft.thesis.conviction == 0
    assert draft.thesis.decision == "no_trade"


def test_agent_cannot_request_trade_before_mcp_research(monkeypatch) -> None:
    premature = _call(
        llm_module.REQUEST_TRADE_TOOL,
        _trade_request(),
        "premature",
    )
    no_follow_up = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="I am done", tool_calls=[])
            )
        ]
    )

    draft, mcp, captured = _run_agent(
        monkeypatch,
        [_response(premature), no_follow_up],
    )

    assert draft.thesis.conviction == 0
    assert draft.thesis.decision == "no_trade"
    assert mcp.calls == []
    assert any(
        message.get("role") == "tool"
        and "At least one MCP research call is required" in message["content"]
        for message in captured[1]["messages"]
    )


def test_agent_gets_one_final_decision_round_after_research_budget(
    monkeypatch,
) -> None:
    research = [
        _response(
            _call(
                "get_stock_snapshot",
                {"symbols": "SPY"},
                f"research-{index}",
            )
        )
        for index in range(4)
    ]
    over_budget = _response(
        _call("get_stock_snapshot", {"symbols": "SPY"}, "over-budget")
    )
    request = _response(
        _call(
            llm_module.REQUEST_TRADE_TOOL,
            _trade_request(),
            "request",
        )
    )

    draft, mcp, captured = _run_agent(
        monkeypatch,
        [*research, over_budget, request],
    )

    assert draft.thesis.conviction == 0.8
    assert draft.research_calls == llm_module.MAX_RESEARCH_CALLS
    assert len(mcp.calls) == llm_module.MAX_RESEARCH_CALLS
    assert any(
        message.get("role") == "tool"
        and "Research budget exhausted" in message["content"]
        for message in captured[-1]["messages"]
    )