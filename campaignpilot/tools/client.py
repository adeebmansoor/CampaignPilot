"""How agents reach their tools.

Both clients have the same `call(name, **args)` interface, so the agent code
doesn't care whether tools run in-process or behind the MCP server.

* MCPToolClient  — starts the MCP server as a subprocess and talks to it over
  stdio using the official MCP Python SDK. One long-lived session runs on a
  background event loop so the synchronous LangGraph nodes can use it.
* DirectToolClient — calls the same functions directly (fast, simple, used in
  some tests and when TOOLS_MODE=direct).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from concurrent.futures import Future
from contextlib import AsyncExitStack
from functools import lru_cache
from typing import Any, Protocol

from campaignpilot.config import ROOT_DIR, get_settings

log = logging.getLogger(__name__)


class ToolClient(Protocol):
    def call(self, name: str, **arguments: Any) -> Any: ...
    def list_tools(self) -> list[str]: ...


class ToolError(RuntimeError):
    pass


class DirectToolClient:
    def __init__(self) -> None:
        from campaignpilot.tools import marketing_tools as t

        self._tools = {
            "get_database_schema": t.get_database_schema,
            "run_segment_query": t.run_segment_query,
            "get_campaign_performance": lambda channel="", top=5: t.get_campaign_performance(channel or None, top),
            "get_channel_benchmarks": t.get_channel_benchmarks,
            "search_marketing_knowledge": t.search_marketing_knowledge,
        }

    def list_tools(self) -> list[str]:
        return sorted(self._tools)

    def call(self, name: str, **arguments: Any) -> Any:
        if name not in self._tools:
            raise ToolError(f"unknown tool {name}")
        try:
            return self._tools[name](**arguments)
        except Exception as e:  # mirror MCP behaviour: tool errors come back as errors
            raise ToolError(str(e)) from e


class MCPToolClient:
    def __init__(self, timeout: float = 60.0) -> None:
        self._timeout = timeout
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="mcp-client")
        self._thread.start()
        self._stack: AsyncExitStack | None = None
        self._session = None
        self._lock = threading.Lock()
        self._run(self._connect())

    def _run(self, coro) -> Any:
        fut: Future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=self._timeout)

    async def _connect(self) -> None:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "campaignpilot.mcp_server.server"],
            env={**os.environ, "PYTHONPATH": str(ROOT_DIR)},
            cwd=str(ROOT_DIR),
        )
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        log.info("connected to MCP server; tools=%s", await self._list())

    async def _list(self) -> list[str]:
        result = await self._session.list_tools()
        return sorted(t.name for t in result.tools)

    def list_tools(self) -> list[str]:
        return self._run(self._list())

    async def _call(self, name: str, arguments: dict) -> Any:
        result = await self._session.call_tool(name, arguments)
        text = "\n".join(getattr(c, "text", "") for c in (result.content or []))
        if getattr(result, "is_error", False) or getattr(result, "isError", False):
            raise ToolError(text or f"tool {name} failed")
        structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
        if structured is not None:
            # list/str returns are wrapped as {"result": ...} by the SDK
            if isinstance(structured, dict) and set(structured) == {"result"}:
                return structured["result"]
            return structured
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return text

    def call(self, name: str, **arguments: Any) -> Any:
        with self._lock:
            return self._run(self._call(name, arguments))

    def close(self) -> None:
        if self._stack:
            try:
                self._run(self._stack.aclose())
            except Exception:  # noqa: BLE001
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)


@lru_cache
def get_tool_client() -> ToolClient:
    mode = get_settings().tools_mode
    if mode == "mcp":
        return MCPToolClient()
    if mode == "direct":
        return DirectToolClient()
    raise ValueError(f"Unknown TOOLS_MODE: {mode}")
