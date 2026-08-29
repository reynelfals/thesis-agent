from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI

from thesis.config import ConfigError, Settings
from thesis.models import Side, Thesis
from thesis.observe import MarketSnapshot
from thesis.risk import MIN_CONVICTION
from thesis.tools.mcp import AlpacaMcpSession, McpError


MAX_AGENT_ROUNDS = 6
MAX_RESEARCH_CALLS = 4
REQUEST_TRADE_TOOL = "request_defined_risk_spread"

SYSTEM = """You are Thesis, an autonomous but tightly constrained options agent.
Use the supplied Alpaca MCP research tools to verify current evidence before making
a decision. You must make at least one MCP research call. You may inspect only the
shortlisted symbols and directions enforced by the tool harness.
For get_option_chain, always set type="call" when researching a bullish thesis or
type="put" when researching a bearish thesis.
You have at most four research calls. Do not repeat a symbol/type query. After at
most four calls, stop researching and immediately request a spread or abstain.

When your research is complete, call request_defined_risk_spread with exactly one
testable directional thesis, or with conviction 0 when no trade is justified.
That call requests a trade; deterministic code independently chooses the contracts,
sizes risk, refreshes quotes, and may reject the request. You cannot submit, cancel,
replace, exercise, or close broker orders directly.

Prefer 14-45 DTE defined-risk debit verticals. No 0DTE, naked shorts, or crypto.
Never invent prices, fills, account state, or tool results.
"""

REQUEST_TRADE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": REQUEST_TRADE_TOOL,
        "description": (
            "Request one defined-risk debit spread for deterministic validation. "
            "This does not bypass the risk or execution harness."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "underlying": {"type": "string"},
                "side": {"type": "string", "enum": ["bullish", "bearish"]},
                "regime": {"type": "string"},
                "setup": {"type": "string"},
                "invalidation": {"type": "string"},
                "horizon": {"type": "string"},
                "expected_move_pct": {"type": "number"},
                "iv_note": {"type": "string"},
                "conviction": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": [
                "underlying",
                "side",
                "regime",
                "setup",
                "invalidation",
                "horizon",
                "expected_move_pct",
                "iv_note",
                "conviction",
            ],
        },
    },
}


@dataclass(frozen=True)
class AgentDraft:
    thesis: Thesis
    traces: list[dict[str, Any]]
    research_calls: int


def grok_client(settings: Settings) -> OpenAI:
    if not settings.xai_api_key:
        raise ConfigError("missing XAI_API_KEY")
    return OpenAI(api_key=settings.xai_api_key, base_url="https://api.x.ai/v1")


def _trace(step: str, *, ok: bool, status: str, **detail: Any) -> dict[str, Any]:
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "tool": "Grok MCP agent",
        "step": step,
        "ok": ok,
        "status": status,
        **detail,
    }


def _payload(
    snaps: list[MarketSnapshot],
    feasible_sides: dict[str, list[str]],
) -> list[dict[str, Any]]:
    return [
        {
            "symbol": snap.symbol,
            "spot": round(snap.spot, 2),
            "sma5": round(snap.sma5, 2),
            "sma20": round(snap.sma20, 2),
            "ret_5d_pct": round(snap.ret_5d_pct, 2),
            "realized_vol_20d": round(snap.realized_vol_20d, 3),
            "avg_dollar_volume_20d": round(snap.avg_dollar_volume_20d, 2),
            "regime": snap.regime,
            "feasible_sides": feasible_sides.get(snap.symbol, []),
        }
        for snap in snaps
    ]


def _no_trade(
    snaps: list[MarketSnapshot],
    *,
    setup: str,
    notes: str,
) -> Thesis:
    fallback = snaps[0] if snaps else None
    return Thesis(
        underlying=fallback.symbol if fallback else "SPY",
        side=Side.BULLISH,
        regime="agent_no_trade",
        setup=setup,
        invalidation="n/a",
        horizon="14-45 DTE",
        expected_move_pct=0,
        iv_note="No executable MCP thesis was produced.",
        conviction=0.0,
        notes=notes,
        decision="no_trade",
    )


def _thesis_from_request(
    data: dict[str, Any],
    *,
    snaps: list[MarketSnapshot],
    feasible_sides: dict[str, list[str]],
    model: str,
) -> Thesis:
    shortlist = tuple(dict.fromkeys(snap.symbol for snap in snaps))
    underlying = str(data.get("underlying", "")).upper()
    if underlying not in shortlist:
        return _no_trade(
            snaps,
            setup="Agent requested an underlying outside the deterministic shortlist.",
            notes="rejected underlying outside shortlist",
        )

    side_raw = str(data.get("side", "")).lower()
    if side_raw not in {Side.BULLISH.value, Side.BEARISH.value}:
        return _no_trade(
            snaps,
            setup="Agent requested an invalid options direction.",
            notes="rejected invalid side",
        )
    side = Side(side_raw)
    if side.value not in feasible_sides.get(underlying, []):
        thesis = _no_trade(
            snaps,
            setup="Agent requested a side outside the feasibility-probed shortlist.",
            notes="rejected side outside feasible_sides",
        )
        thesis.underlying = underlying
        thesis.side = side
        return thesis

    try:
        conviction = float(data.get("conviction", 0))
        expected_move = float(data.get("expected_move_pct", 0) or 0)
    except (TypeError, ValueError):
        return _no_trade(
            snaps,
            setup="Agent returned non-numeric conviction or expected move.",
            notes="rejected malformed numeric fields",
        )
    conviction = max(0.0, min(1.0, conviction))
    return Thesis(
        underlying=underlying,
        side=side,
        regime=str(data.get("regime", "")),
        setup=str(data.get("setup", "")),
        invalidation=str(data.get("invalidation", "")),
        horizon=str(data.get("horizon", "14-45 DTE")),
        expected_move_pct=expected_move,
        iv_note=str(data.get("iv_note", "IV rank not supplied")),
        conviction=conviction,
        notes=f"model={model} skip={conviction < MIN_CONVICTION} agent=mcp",
    )


def _assistant_message(message: Any) -> dict[str, Any]:
    calls = []
    for call in getattr(message, "tool_calls", None) or []:
        calls.append(
            {
                "id": str(call.id),
                "type": "function",
                "function": {
                    "name": str(call.function.name),
                    "arguments": str(call.function.arguments or "{}"),
                },
            }
        )
    return {
        "role": "assistant",
        "content": getattr(message, "content", None),
        "tool_calls": calls,
    }


async def draft_thesis(
    settings: Settings,
    snaps: list[MarketSnapshot],
    feasible_sides: dict[str, list[str]],
    mcp: AlpacaMcpSession,
) -> AgentDraft:
    shortlist = tuple(dict.fromkeys(snap.symbol for snap in snaps))
    traces: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": (
                "Deterministic shortlist: "
                + ", ".join(shortlist)
                + "\nPrecomputed evidence:\n"
                + json.dumps(_payload(snaps, feasible_sides), indent=2)
                + "\nResearch the finalists with Alpaca MCP, then call "
                + REQUEST_TRADE_TOOL
                + "."
            ),
        },
    ]
    tools = [*mcp.model_tools(), REQUEST_TRADE_SCHEMA]
    client = grok_client(settings)
    research_calls = 0

    for round_index in range(1, MAX_AGENT_ROUNDS + 1):
        response = client.chat.completions.create(
            model=settings.grok_model,
            temperature=0.2,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        message = response.choices[0].message
        tool_calls = list(getattr(message, "tool_calls", None) or [])
        if not tool_calls:
            traces.append(
                _trace(
                    "tool loop",
                    ok=False,
                    status="Agent returned no tool request; failed closed",
                    round=round_index,
                )
            )
            return AgentDraft(
                _no_trade(
                    snaps,
                    setup="Grok did not complete the required MCP tool workflow.",
                    notes="agent returned no tool call",
                ),
                traces,
                research_calls,
            )

        messages.append(_assistant_message(message))
        for call in tool_calls:
            name = str(call.function.name)
            try:
                arguments = json.loads(call.function.arguments or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments must be an object")
            except (TypeError, ValueError, json.JSONDecodeError):
                traces.append(
                    _trace(
                        name,
                        ok=False,
                        status="Malformed tool arguments; failed closed",
                        round=round_index,
                    )
                )
                return AgentDraft(
                    _no_trade(
                        snaps,
                        setup="Grok produced malformed MCP tool arguments.",
                        notes="malformed agent tool arguments",
                    ),
                    traces,
                    research_calls,
                )

            if name == REQUEST_TRADE_TOOL:
                if research_calls < 1:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(call.id),
                            "content": json.dumps(
                                {
                                    "accepted": False,
                                    "reason": "At least one MCP research call is required.",
                                }
                            ),
                        }
                    )
                    traces.append(
                        _trace(
                            name,
                            ok=False,
                            status="Rejected: no MCP research call preceded the request",
                            round=round_index,
                        )
                    )
                    continue
                thesis = _thesis_from_request(
                    arguments,
                    snaps=snaps,
                    feasible_sides=feasible_sides,
                    model=settings.grok_model,
                )
                traces.append(
                    _trace(
                        name,
                        ok=thesis.conviction > 0,
                        status=(
                            f"Requested {thesis.underlying} {thesis.side.value} "
                            f"defined-risk spread; conviction={thesis.conviction:.2f}"
                            if thesis.conviction > 0
                            else "Agent requested no trade or violated the shortlist contract"
                        ),
                        round=round_index,
                        underlying=thesis.underlying,
                        side=thesis.side.value,
                        conviction=thesis.conviction,
                    )
                )
                return AgentDraft(thesis, traces, research_calls)

            if research_calls >= MAX_RESEARCH_CALLS:
                traces.append(
                    _trace(
                        name,
                        ok=False,
                        status="Research tool budget exhausted; agent must decide now",
                        round=round_index,
                    )
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call.id),
                        "content": json.dumps(
                            {
                                "accepted": False,
                                "reason": (
                                    "Research budget exhausted. Call "
                                    f"{REQUEST_TRADE_TOOL} now with a trade or "
                                    "conviction 0."
                                ),
                            }
                        ),
                    }
                )
                continue
            try:
                result = await mcp.call_model_tool(
                    name,
                    arguments,
                    allowed_symbols=set(shortlist),
                    feasible_sides=feasible_sides,
                )
            except McpError:
                traces.append(
                    _trace(
                        name,
                        ok=False,
                        status="MCP research call was rejected or unavailable",
                        round=round_index,
                    )
                )
                return AgentDraft(
                    _no_trade(
                        snaps,
                        setup="Required Alpaca MCP research was unavailable.",
                        notes="sanitized MCP research failure",
                    ),
                    traces,
                    research_calls,
                )
            research_calls += 1
            traces.append(
                _trace(
                    name,
                    ok=result.ok,
                    status=result.status,
                    round=round_index,
                )
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(call.id),
                    "content": json.dumps(result.data, default=str),
                }
            )

    traces.append(
        _trace(
            "tool loop",
            ok=False,
            status=f"Agent exceeded {MAX_AGENT_ROUNDS} rounds; failed closed",
        )
    )
    return AgentDraft(
        _no_trade(
            snaps,
            setup="Grok did not finish the bounded MCP workflow.",
            notes="agent round limit exhausted",
        ),
        traces,
        research_calls,
    )