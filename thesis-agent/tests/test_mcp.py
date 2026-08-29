from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from thesis.tools.mcp import (
    MCP_RUNTIME_ENV_KEYS,
    MAX_MCP_RESULT_BYTES,
    AlpacaMcpSession,
    McpError,
    REQUIRED_TOOLS,
)


class Settings:
    api_key = "paper-key"
    secret_key = "paper-secret"


class Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return None


class FakeSession:
    responses = {}
    tools = []
    calls = []

    def __init__(self, read, write):
        self.calls = []
        FakeSession.calls = self.calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def initialize(self):
        return None

    async def list_tools(self):
        return SimpleNamespace(tools=self.tools)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        value = self.responses[name]
        if isinstance(value, BaseException):
            raise value
        return value


def tool(name):
    if name == "get_stock_snapshot":
        properties = {"symbols": {"type": "string"}}
        required = ["symbols"]
    elif name == "get_option_chain":
        properties = {
            "underlying_symbol": {"type": "string"},
            "type": {"type": "string", "enum": ["call", "put"]},
            "limit": {"type": "integer", "maximum": 1000},
            "strike_price_gte": {"type": "number"},
            "strike_price_lte": {"type": "number"},
            "feed": {"type": "string"},
        }
        required = ["underlying_symbol"]
    else:
        properties = {"symbol": {"type": "string"}}
        required = []
    return SimpleNamespace(
        name=name,
        description=f"{name} description",
        inputSchema={
            "type": "object",
            "properties": properties,
            "required": required,
        },
    )


@pytest.fixture
def mcp(monkeypatch):
    FakeSession.tools = [tool(name) for name in REQUIRED_TOOLS] + [tool("cancel_all_orders")]
    FakeSession.responses = {
        name: SimpleNamespace(
            isError=False,
            structuredContent={
                "_alpaca_mcp_security": {"account_id": "hidden-account"},
                "data": {"ok": True},
            },
        )
        for name in REQUIRED_TOOLS
    }
    captured = {}

    def fake_stdio(params):
        captured["params"] = params
        return Context(("read", "write"))

    monkeypatch.setattr("thesis.tools.mcp.shutil.which", lambda _: "/safe/alpaca-mcp-server")
    monkeypatch.setattr("thesis.tools.mcp.stdio_client", fake_stdio)
    monkeypatch.setattr("thesis.tools.mcp.ClientSession", FakeSession)
    return captured


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_discovery_filters_model_tools_and_sets_paper_env(mcp):
    async with AlpacaMcpSession(Settings()) as session:
        names = [item["function"]["name"] for item in session.model_tools()]
        assert names == ["get_stock_snapshot", "get_option_chain"]
        assert session.model_tools()[0]["function"]["parameters"]["type"] == "object"
        option_schema = session.model_tools()[1]["function"]["parameters"]
        assert option_schema["required"] == ["underlying_symbol", "type"]
        assert option_schema["additionalProperties"] is False
        assert "feed" not in option_schema["properties"]
        assert option_schema["properties"]["limit"]["maximum"] == 20

    env = mcp["params"].env
    assert env["ALPACA_API_KEY"] == "paper-key"
    assert env["ALPACA_SECRET_KEY"] == "paper-secret"
    assert env["ALPACA_PAPER_TRADE"] == "true"
    assert env["ALPACA_TOOLSETS"] == "account,trading,assets,stock-data,options-data"
    assert set(env) <= MCP_RUNTIME_ENV_KEYS | {
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "ALPACA_PAPER_TRADE",
        "ALPACA_TOOLSETS",
    }
    assert "XAI_API_KEY" not in env
    assert "SESSION_SECRET" not in env
    assert "APCA_API_KEY_ID" not in env
    assert "APCA_API_SECRET_KEY" not in env
    assert "paper-key" not in str(session.traces)
    assert "paper-secret" not in str(session.traces)
    assert "hidden-account" not in str(session.traces)
    for trace in session.traces:
        assert {"at", "tool", "step", "ok", "status"} <= trace.keys()
        assert trace["tool"] == "Alpaca MCP"


@pytest.mark.anyio
async def test_mcp_child_does_not_inherit_unrelated_parent_secrets(
    mcp,
    monkeypatch,
):
    monkeypatch.setenv("XAI_API_KEY", "must-not-reach-child")
    monkeypatch.setenv("SESSION_SECRET", "must-not-reach-child")
    monkeypatch.setenv("UNRELATED_PRIVATE_TOKEN", "must-not-reach-child")

    async with AlpacaMcpSession(Settings()):
        pass

    env = mcp["params"].env
    assert "must-not-reach-child" not in str(env)
    assert "XAI_API_KEY" not in env
    assert "SESSION_SECRET" not in env
    assert "UNRELATED_PRIVATE_TOKEN" not in env


@pytest.mark.anyio
async def test_discovery_fails_closed_when_required_tool_missing(mcp):
    FakeSession.tools = [tool(name) for name in REQUIRED_TOOLS - {"get_clock"}]
    with pytest.raises(McpError, match="get_clock"):
        async with AlpacaMcpSession(Settings()):
            pass


@pytest.mark.anyio
async def test_allowlists_and_shortlist_are_enforced(mcp):
    async with AlpacaMcpSession(Settings()) as session:
        with pytest.raises(McpError):
            await session.call_system_tool("cancel_all_orders", {})
        with pytest.raises(McpError):
            await session.call_model_tool(
                "place_option_order",
                {},
                allowed_symbols={"AAPL"},
                feasible_sides={"AAPL": ["bullish"]},
            )
        with pytest.raises(McpError):
            await session.call_model_tool(
                "get_stock_snapshot",
                {"symbols": "AAPL,MSFT,SPY,NVDA"},
                allowed_symbols={"AAPL", "MSFT", "SPY", "NVDA"},
                feasible_sides={},
            )
        with pytest.raises(McpError):
            await session.call_model_tool(
                "get_stock_snapshot",
                {"symbols": "TSLA"},
                allowed_symbols={"AAPL"},
                feasible_sides={},
            )


@pytest.mark.anyio
async def test_option_chain_forces_dte_limit_and_direction(mcp):
    async with AlpacaMcpSession(Settings()) as session:
        await session.call_model_tool(
            "get_option_chain",
            {"underlying_symbol": "aapl", "type": "call", "limit": 100},
            allowed_symbols={"AAPL"},
            feasible_sides={"AAPL": ["bullish"]},
        )
        sent = FakeSession.calls[-1][1]
        assert sent["limit"] == 20
        assert sent["type"] == "call"
        assert sent["expiration_date_gte"] < sent["expiration_date_lte"]
        with pytest.raises(McpError):
            await session.call_model_tool(
                "get_option_chain",
                {"underlying_symbol": "AAPL", "type": "put"},
                allowed_symbols={"AAPL"},
                feasible_sides={"AAPL": ["bullish"]},
            )


@pytest.mark.anyio
async def test_locally_blocked_model_call_has_safe_audit_trace(mcp):
    async with AlpacaMcpSession(Settings()) as session:
        with pytest.raises(McpError):
            await session.call_model_tool(
                "get_option_chain",
                {
                    "underlying_symbol": "AAPL",
                    "type": "put",
                    "limit": 99,
                    "authorization": "Bearer model-secret",
                    "prompt": "arbitrary untrusted field",
                    "account_number": "123456789",
                },
                allowed_symbols={"AAPL"},
                feasible_sides={"AAPL": ["bullish"]},
            )

    trace = session.traces[-1]
    assert trace["step"] == "get_option_chain"
    assert trace["ok"] is False
    assert trace["status"] == "Model MCP call blocked by local policy"
    assert trace["arguments"] == {
        "underlying_symbol": "AAPL",
        "type": "put",
        "limit": 99,
    }
    serialized = str(trace)
    assert "model-secret" not in serialized
    assert "arbitrary untrusted field" not in serialized
    assert "123456789" not in serialized


def order():
    return {
        "qty": "1",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": "1.25",
        "client_order_id": "thesis-abc123",
        "order_class": "mleg",
        "legs": [
            {
                "symbol": "AAPL260117C00150000",
                "ratio_qty": "1",
                "side": "buy",
                "position_intent": "buy_to_open",
            },
            {
                "symbol": "AAPL260117C00160000",
                "ratio_qty": "1",
                "side": "sell",
                "position_intent": "sell_to_open",
            },
        ],
    }


@pytest.mark.anyio
async def test_order_is_canonical_and_dispatched_only_once(mcp):
    async with AlpacaMcpSession(Settings()) as session:
        await session.place_option_order(order())
        assert [name for name, _ in FakeSession.calls].count("place_option_order") == 1
        with pytest.raises(McpError, match="already dispatched"):
            await session.place_option_order(order())
        assert [name for name, _ in FakeSession.calls].count("place_option_order") == 1


@pytest.mark.anyio
async def test_order_error_is_not_retried(mcp):
    FakeSession.responses["place_option_order"] = TimeoutError("secret account 123456")
    async with AlpacaMcpSession(Settings()) as session:
        with pytest.raises(McpError):
            await session.place_option_order(order())
        with pytest.raises(McpError, match="already dispatched"):
            await session.place_option_order(order())
        assert [name for name, _ in FakeSession.calls].count("place_option_order") == 1
        assert "123456" not in str(session.traces)


@pytest.mark.anyio
async def test_result_and_traces_are_bounded_and_sanitized(mcp):
    FakeSession.responses["get_stock_snapshot"] = SimpleNamespace(
        isError=False,
        structuredContent={
            "_alpaca_mcp_security": {
                "account_id": "security-account-id",
                "authorization": "Bearer secret",
            },
            "data": {
                "account_number": "123456789",
                "accountId": "camel-account-id",
                "nested": {
                    "apiKey": "camel-api-key",
                    "secretKey": "camel-secret-key",
                    "securityContext": {"credentialValue": "hidden"},
                },
                "rows": list(range(100)),
                "text": "x" * 1000,
            }
        },
    )
    async with AlpacaMcpSession(Settings()) as session:
        result = await session.call_model_tool(
            "get_stock_snapshot",
            {"symbols": ["AAPL"]},
            allowed_symbols={"AAPL"},
            feasible_sides={},
        )
    assert len(result.data["rows"]) == 20
    assert len(result.data["text"]) == 500
    assert "_alpaca_mcp_security" not in result.data
    result_serialized = json.dumps(result.data)
    serialized = str(session.traces) + str(result.as_trace()) + result_serialized
    assert "123456789" not in serialized
    assert "camel-account-id" not in serialized
    assert "camel-api-key" not in serialized
    assert "camel-secret-key" not in serialized
    assert "securityContext" not in result_serialized
    assert "credentialValue" not in serialized
    assert "security-account-id" not in serialized
    assert "Bearer secret" not in serialized
    assert "paper-secret" not in serialized
    for trace in session.traces:
        assert {"at", "tool", "step", "ok", "status"} <= trace.keys()


@pytest.mark.anyio
async def test_pathological_result_has_a_global_serialized_size_cap(mcp):
    huge = {
        f"outer-{outer}-{'k' * 80}": {
            f"inner-{inner}-{'j' * 80}": "x" * 500
            for inner in range(50)
        }
        for outer in range(50)
    }
    FakeSession.responses["get_stock_snapshot"] = SimpleNamespace(
        isError=False,
        structuredContent={"data": huge},
    )
    async with AlpacaMcpSession(Settings()) as session:
        result = await session.call_model_tool(
            "get_stock_snapshot",
            {"symbols": "AAPL"},
            allowed_symbols={"AAPL"},
            feasible_sides={},
        )

    encoded = json.dumps(result.data, separators=(",", ":")).encode("utf-8")
    assert len(encoded) <= MAX_MCP_RESULT_BYTES
    assert result.data["truncated"] is True