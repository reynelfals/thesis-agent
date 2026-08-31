from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import re
import shutil
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from thesis.config import Settings


class McpError(RuntimeError):
    """A fail-closed error at the Alpaca MCP trust boundary."""


SYSTEM_TOOLS = frozenset({"get_account_info", "get_clock", "get_order_by_id"})
MODEL_TOOLS = frozenset({"get_stock_snapshot", "get_option_chain"})
REQUIRED_TOOLS = SYSTEM_TOOLS | MODEL_TOOLS | {"place_option_order"}
TOOLSETS = "account,trading,assets,stock-data,options-data"
MAX_MCP_RESULT_BYTES = 96 * 1024
MAX_MCP_RESULT_NODES = 2_000
MAX_MCP_TEXT_CHARS = 48_000
MCP_RUNTIME_ENV_KEYS = frozenset(
    {
        "CURL_CA_BUNDLE",
        "HOME",
        "LANG",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "NIX_LD",
        "NIX_LD_LIBRARY_PATH",
        "NIX_SSL_CERT_FILE",
        "PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONUSERBASE",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "TMPDIR",
        "TZ",
        "XDG_CACHE_HOME",
    }
)
_SENSITIVE_KEY_NAMES = {
    "alpacamcpsecurity",
    "accountid",
    "accountnumber",
    "apikey",
    "apcaapikeyid",
    "apcaapisecretkey",
    "authorization",
    "secret",
    "secretkey",
}


def _trace(step: str, ok: bool, status: str, **metadata: Any) -> dict[str, Any]:
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "tool": "Alpaca MCP",
        "step": step,
        "ok": ok,
        "status": status[:160],
        **_bound(metadata),
    }


def _result_summary(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return {"kind": "object", "field_count": min(len(data), 50)}
    if isinstance(data, list):
        return {"kind": "array", "item_count": min(len(data), 20)}
    return {"kind": type(data).__name__}


def _safe_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in arguments.items():
        if _is_sensitive_key(str(key)):
            continue
        if key in {"symbol", "underlying_symbol", "type", "limit"}:
            safe[key] = _bound(value, depth=0)
        elif key in {"symbols", "expiration_date_gte", "expiration_date_lte"}:
            safe[key] = _bound(value, depth=0)
    return safe


def _is_sensitive_key(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    return (
        normalized in _SENSITIVE_KEY_NAMES
        or normalized.endswith(("accountid", "accountnumber", "apikey", "secretkey"))
        or "authorization" in normalized
        or "credential" in normalized
        or "password" in normalized
        or "secret" in normalized
        or "security" in normalized
        or "token" in normalized
        or "cookie" in normalized
    )


@dataclass
class _BoundBudget:
    nodes: int = 0
    text_chars: int = 0


def _bound(value: Any, *, depth: int = 0) -> Any:
    """Globally bound and redact untrusted MCP data."""
    bounded = _bound_value(value, depth=depth, budget=_BoundBudget())
    try:
        encoded = json.dumps(
            bounded,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        return {"truncated": True, "summary": _result_summary(value)}
    if len(encoded) > MAX_MCP_RESULT_BYTES:
        return {"truncated": True, "summary": _result_summary(value)}
    return bounded


def _bound_value(value: Any, *, depth: int, budget: _BoundBudget) -> Any:
    budget.nodes += 1
    if budget.nodes > MAX_MCP_RESULT_NODES:
        return "[truncated]"
    if depth >= 6:
        return "[truncated]"
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in list(value.items())[:50]:
            name = str(key)
            if _is_sensitive_key(name):
                continue
            if budget.nodes >= MAX_MCP_RESULT_NODES:
                output["truncated"] = True
                break
            output[name[:100]] = _bound_value(
                item,
                depth=depth + 1,
                budget=budget,
            )
        return output
    if isinstance(value, (list, tuple)):
        output = []
        for item in value[:20]:
            if budget.nodes >= MAX_MCP_RESULT_NODES:
                output.append("[truncated]")
                break
            output.append(
                _bound_value(item, depth=depth + 1, budget=budget)
            )
        return output
    if isinstance(value, str):
        if re.search(r"(?i)\bbearer\s+\S+", value):
            return "[redacted]"
        remaining = MAX_MCP_TEXT_CHARS - budget.text_chars
        if remaining <= 0:
            return "[truncated]"
        text = value[: min(500, remaining)]
        budget.text_chars += len(text)
        return text
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


@dataclass(frozen=True)
class McpToolResult:
    name: str
    ok: bool
    data: Any
    status: str

    def as_trace(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "status": self.status[:160],
            "result": _result_summary(self.data),
        }


class AlpacaMcpSession:
    def __init__(self, settings: Settings, *, timeout: float = 30.0) -> None:
        self.settings = settings
        self.timeout = timeout
        self.traces: list[dict[str, Any]] = []
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._tools: dict[str, Any] = {}
        self._order_dispatched = False

    async def __aenter__(self) -> AlpacaMcpSession:
        binary = shutil.which("alpaca-mcp-server")
        if not binary:
            raise McpError("official alpaca-mcp-server is not installed")

        env = {
            key: os.environ[key]
            for key in MCP_RUNTIME_ENV_KEYS
            if os.environ.get(key)
        }
        env["ALPACA_API_KEY"] = self.settings.api_key
        env["ALPACA_SECRET_KEY"] = self.settings.secret_key
        env["ALPACA_PAPER_TRADE"] = "true"
        env["ALPACA_TOOLSETS"] = TOOLSETS
        params = StdioServerParameters(
            command=binary,
            args=["--transport", "stdio"],
            env=env,
        )
        self.traces.append(
            _trace(
                "server",
                True,
                "Starting official Alpaca MCP server over stdio",
                server=Path(binary).name,
                transport="stdio",
                paper=True,
                toolsets=TOOLSETS.split(","),
            )
        )
        stack = AsyncExitStack()
        self._stack = stack
        try:
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            self._session = session
            await asyncio.wait_for(session.initialize(), timeout=self.timeout)
            response = await asyncio.wait_for(session.list_tools(), timeout=self.timeout)
            self._tools = {tool.name: tool for tool in response.tools}
            missing = sorted(REQUIRED_TOOLS - self._tools.keys())
            self.traces.append(
                _trace(
                    "discovery",
                    not missing,
                    (
                        "Required Alpaca MCP tools discovered"
                        if not missing
                        else "Required Alpaca MCP tools are missing"
                    ),
                    required_count=len(REQUIRED_TOOLS),
                    available_required=sorted(REQUIRED_TOOLS & self._tools.keys()),
                    missing=missing,
                )
            )
            if missing:
                raise McpError(
                    "Alpaca MCP server is missing required tools: " + ", ".join(missing)
                )
            return self
        except BaseException as exc:
            await stack.aclose()
            self._stack = None
            self._session = None
            if isinstance(exc, McpError):
                raise
            raise McpError("could not initialize Alpaca MCP server") from exc

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    def model_tools(self) -> list[dict[str, Any]]:
        if not self._tools:
            raise McpError("Alpaca MCP session is not initialized")
        output = []
        for name in ("get_stock_snapshot", "get_option_chain"):
            tool = self._tools[name]
            schema = copy.deepcopy(
                getattr(tool, "inputSchema", None)
                or getattr(tool, "input_schema", None)
                or {"type": "object", "properties": {}}
            )
            discovered_properties = schema.get("properties", {})
            if name == "get_stock_snapshot":
                allowed_fields = ("symbols",)
                required = ["symbols"]
            else:
                allowed_fields = (
                    "underlying_symbol",
                    "type",
                    "strike_price_gte",
                    "strike_price_lte",
                    "limit",
                )
                required = ["underlying_symbol", "type"]
            properties = {
                field: copy.deepcopy(discovered_properties[field])
                for field in allowed_fields
                if field in discovered_properties
            }
            if name == "get_option_chain" and "limit" in properties:
                properties["limit"]["maximum"] = 20
                properties["limit"]["default"] = 20
            schema = {
                "type": "object",
                "additionalProperties": False,
                "properties": properties,
                "required": required,
            }
            description = str(getattr(tool, "description", "") or "")
            if name == "get_option_chain":
                description += (
                    " The type field is required: use call for a bullish thesis "
                    "and put for a bearish thesis."
                )
            output.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description,
                        "parameters": schema,
                    },
                }
            )
        return output

    async def call_system_tool(
        self,
        name: str,
        args: Mapping[str, Any],
    ) -> McpToolResult:
        if name not in SYSTEM_TOOLS:
            raise McpError(f"system MCP tool is not allowed: {name}")
        return await self._call(name, dict(args), trace_arguments=False)

    async def call_model_tool(
        self,
        name: str,
        args: Mapping[str, Any],
        *,
        allowed_symbols: Iterable[str],
        feasible_sides: Mapping[str, Iterable[str]],
    ) -> McpToolResult:
        try:
            arguments = self._model_arguments(
                name,
                args,
                allowed_symbols=allowed_symbols,
                feasible_sides=feasible_sides,
            )
        except McpError:
            self.traces.append(
                _trace(
                    name,
                    False,
                    "Model MCP call blocked by local policy",
                    arguments=_safe_arguments(args),
                )
            )
            raise
        return await self._call(name, arguments, trace_arguments=True)

    def _model_arguments(
        self,
        name: str,
        args: Mapping[str, Any],
        *,
        allowed_symbols: Iterable[str],
        feasible_sides: Mapping[str, Iterable[str]],
    ) -> dict[str, Any]:
        if name not in MODEL_TOOLS:
            raise McpError(f"model MCP tool is not allowed: {name}")
        arguments = dict(args)
        allowed = {str(symbol).strip().upper() for symbol in allowed_symbols}

        if name == "get_stock_snapshot":
            raw_symbols = arguments.get("symbols")
            if isinstance(raw_symbols, str):
                symbols = [part.strip().upper() for part in raw_symbols.split(",")]
            elif isinstance(raw_symbols, (list, tuple)):
                symbols = [str(part).strip().upper() for part in raw_symbols]
            else:
                raise McpError("stock snapshot requires symbols")
            symbols = [symbol for symbol in symbols if symbol]
            if not symbols or len(symbols) > 3 or any(s not in allowed for s in symbols):
                raise McpError("stock snapshot symbols violate the shortlist")
            # The official endpoint accepts its symbols query as a comma-separated string.
            return {"symbols": ",".join(dict.fromkeys(symbols))}
        else:
            symbol = str(
                arguments.get("underlying_symbol") or arguments.get("symbol") or ""
            ).strip().upper()
            if not symbol or symbol not in allowed:
                raise McpError("option chain symbol violates the shortlist")
            sides = {str(side).lower() for side in feasible_sides.get(symbol, [])}
            requested_type = str(arguments.get("type") or "").lower()
            required_type = (
                "call"
                if sides == {"bullish"}
                else "put"
                if sides == {"bearish"}
                else None
            )
            if required_type is not None and requested_type not in {"", required_type}:
                raise McpError("option type violates the feasible side")
            if requested_type not in {"call", "put"}:
                if required_type is None:
                    raise McpError("option chain requires call or put type")
                requested_type = required_type
            try:
                limit = min(20, max(1, int(arguments.get("limit", 20))))
            except (TypeError, ValueError) as exc:
                raise McpError("option chain limit must be an integer") from exc
            validated = {
                "underlying_symbol": symbol,
                "type": requested_type,
                "limit": limit,
            }
            for field in ("strike_price_gte", "strike_price_lte"):
                if field not in arguments:
                    continue
                try:
                    strike = float(arguments[field])
                except (TypeError, ValueError) as exc:
                    raise McpError("option chain strike bounds must be numbers") from exc
                if not math.isfinite(strike) or strike <= 0:
                    raise McpError("option chain strike bounds must be positive")
                validated[field] = strike
            today = date.today()
            validated["expiration_date_gte"] = (
                today + timedelta(days=14)
            ).isoformat()
            validated["expiration_date_lte"] = (
                today + timedelta(days=45)
            ).isoformat()
            return validated

        raise McpError("model MCP tool is unavailable")

    async def place_option_order(
        self,
        payload: Mapping[str, Any],
    ) -> McpToolResult:
        if self._order_dispatched:
            raise McpError("an option order was already dispatched in this MCP session")
        body = dict(payload)
        single_required = {
            "symbol",
            "qty",
            "side",
            "type",
            "time_in_force",
            "limit_price",
            "position_intent",
            "client_order_id",
        }
        if set(body) == single_required:
            if (
                body["type"] != "limit"
                or body["time_in_force"] != "day"
                or body["side"] != "buy"
                or body["position_intent"] != "buy_to_open"
            ):
                raise McpError("single option order must be limit/day/buy_to_open")
            try:
                qty = float(body["qty"])
                limit_price = float(body["limit_price"])
                if (
                    isinstance(body["qty"], bool)
                    or not math.isfinite(qty)
                    or qty <= 0
                    or not qty.is_integer()
                    or not math.isfinite(limit_price)
                    or limit_price <= 0
                ):
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise McpError(
                    "single option qty must be a positive integer and limit must be finite positive"
                ) from exc
            if not str(body["symbol"]).strip():
                raise McpError("option order symbol is required")
            client_id = str(body["client_order_id"])
            if not client_id.startswith("thesis-") or len(client_id) > 48:
                raise McpError("client_order_id must use the thesis- prefix")
            self._order_dispatched = True
            return await self._call("place_option_order", body, trace_arguments=False)

        required = {
            "qty",
            "type",
            "time_in_force",
            "limit_price",
            "client_order_id",
            "order_class",
            "legs",
        }
        if set(body) != required:
            raise McpError("option order does not have the canonical multi-leg shape")
        if body["type"] != "limit" or body["time_in_force"] != "day":
            raise McpError("option order must be a day limit order")
        if body["order_class"] != "mleg":
            raise McpError("option order must use order_class=mleg")
        try:
            if float(body["qty"]) <= 0 or float(body["limit_price"]) <= 0:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise McpError("option order qty and debit limit must be positive") from exc
        client_id = str(body["client_order_id"])
        if not client_id.startswith("thesis-") or len(client_id) > 48:
            raise McpError("client_order_id must use the thesis- prefix")
        legs = body["legs"]
        if not isinstance(legs, list) or len(legs) != 2:
            raise McpError("option order must have exactly two legs")
        intents: set[str] = set()
        for leg in legs:
            if not isinstance(leg, dict) or set(leg) != {
                "symbol",
                "ratio_qty",
                "side",
                "position_intent",
            }:
                raise McpError("option order leg does not have the canonical shape")
            if str(leg["ratio_qty"]) != "1":
                raise McpError("option order legs must have a 1:1 ratio")
            intent = str(leg["position_intent"])
            side = str(leg["side"])
            if (intent, side) not in {
                ("buy_to_open", "buy"),
                ("sell_to_open", "sell"),
            }:
                raise McpError("option order leg side and position intent disagree")
            if not str(leg["symbol"]).strip():
                raise McpError("option order leg symbol is required")
            intents.add(intent)
        if intents != {"buy_to_open", "sell_to_open"}:
            raise McpError("option order requires one buy and one sell opening leg")
        # Mark immediately before I/O. A timeout or error must never trigger a retry.
        self._order_dispatched = True
        return await self._call("place_option_order", body, trace_arguments=False)

    async def _call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        trace_arguments: bool,
    ) -> McpToolResult:
        if self._session is None or name not in self._tools:
            raise McpError("Alpaca MCP tool is unavailable")
        metadata: dict[str, Any] = {}
        if trace_arguments:
            metadata["arguments"] = _safe_arguments(arguments)
        try:
            response = await asyncio.wait_for(
                self._session.call_tool(name, arguments=arguments),
                timeout=self.timeout,
            )
            if getattr(response, "isError", False):
                raise McpError(f"Alpaca MCP tool reported an error: {name}")
            structured = getattr(response, "structuredContent", None)
            if not isinstance(structured, dict):
                raise McpError(f"Alpaca MCP tool returned no structured response: {name}")
            # Official server responses include security metadata alongside data.
            # The metadata is trust-boundary bookkeeping and must never reach Grok.
            if "_alpaca_mcp_security" in structured and "data" not in structured:
                raise McpError(f"Alpaca MCP tool returned no data response: {name}")
            data: Any = structured["data"] if "data" in structured else structured
            while isinstance(data, dict) and len(data) == 1:
                if "data" in data:
                    data = data["data"]
                elif "result" in data:
                    data = data["result"]
                else:
                    break
            if isinstance(data, dict) and "error" in data:
                raise McpError(f"Alpaca MCP tool returned an error envelope: {name}")
            bounded = _bound(data)
            result = McpToolResult(
                name=name,
                ok=True,
                data=bounded,
                status="Alpaca MCP response received",
            )
            self.traces.append(
                _trace(
                    name,
                    True,
                    "Alpaca MCP response received",
                    **metadata,
                    result=_result_summary(bounded),
                )
            )
            return result
        except BaseException as exc:
            self.traces.append(
                _trace(
                    name,
                    False,
                    "Alpaca MCP call failed",
                    **metadata,
                )
            )
            if isinstance(exc, McpError):
                raise
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise McpError(f"Alpaca MCP call failed: {name}") from exc