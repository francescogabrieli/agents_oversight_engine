from __future__ import annotations

import asyncio
import atexit
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass
from threading import Event, Lock, Thread
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult

from ...infrastructure.environment import get_env_value
from ..registry import ExternalCapabilityRegistry, MCPTransportKind


class MCPPoolClosedError(RuntimeError):
    pass


class MCPCallError(RuntimeError):
    # Connector errors expose whether a side effect might already have started.
    def __init__(self, message: str, *, outcome_may_be_indeterminate: bool) -> None:
        super().__init__(message)
        self.outcome_may_be_indeterminate = outcome_may_be_indeterminate


class MCPCallTimeout(MCPCallError):
    pass


@dataclass
class _PooledSession:
    # Session state lives only on the pool event-loop thread.
    stack: AsyncExitStack
    session: ClientSession
    available_tools: frozenset[str]
    call_lock: asyncio.Lock


class MCPConnectionPool:
    # One composition-root-owned pool reuses healthy official-SDK client sessions.
    def __init__(self, *, registry: ExternalCapabilityRegistry) -> None:
        self._registry = registry
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sessions: dict[str, _PooledSession] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._state_lock = Lock()
        self._ready: Event | None = None
        self._closed = False
        self._thread: Thread | None = None
        atexit.register(self.close)

    def _ensure_started(self) -> None:
        with self._state_lock:
            if self._closed:
                raise MCPPoolClosedError("MCP connection pool is closed.")
            if self._thread is not None and self._thread.is_alive() and self._loop is not None and self._loop.is_running():
                return
            self._loop = asyncio.new_event_loop()
            self._ready = Event()
            self._thread = Thread(
                target=self._run_loop,
                name="layer2-external-mcp-pool",
                daemon=True,
            )
            self._thread.start()
            if not self._ready.wait(timeout=5):
                raise RuntimeError("MCP connection pool event loop did not start.")

    # Invoke one registered tool on a reusable session with a bounded timeout.
    def call_tool(
        self,
        *,
        server_id: str,
        tool_name: str,
        arguments: dict[str, object],
        timeout_seconds: float,
    ) -> CallToolResult:
        self._ensure_started()
        server = self._registry.mcp_servers.get(server_id)
        if server is None or not server.enabled:
            raise MCPCallError(
                "Configured MCP server is unavailable.",
                outcome_may_be_indeterminate=False,
            )
        if tool_name not in server.allowed_tools:
            raise MCPCallError(
                "MCP tool is not allowlisted for the configured server.",
                outcome_may_be_indeterminate=False,
            )
        future = asyncio.run_coroutine_threadsafe(
            self._call_tool(
                server_id=server_id,
                tool_name=tool_name,
                arguments=arguments,
                timeout_seconds=timeout_seconds,
            ),
            self._loop,
        )
        try:
            return future.result(timeout=timeout_seconds + 3)
        except TimeoutError as exc:
            future.cancel()
            self._schedule_drop(server_id)
            raise MCPCallTimeout(
                "MCP tool invocation timed out.",
                outcome_may_be_indeterminate=True,
            ) from exc

    # Close all sessions and stop the owned event-loop thread.
    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        if self._loop is not None and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._close_all(), self._loop)
            try:
                future.result(timeout=10)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10)

    # Create or reuse one healthy session and serialize calls per stdio process.
    async def _call_tool(
        self,
        *,
        server_id: str,
        tool_name: str,
        arguments: dict[str, object],
        timeout_seconds: float,
    ) -> CallToolResult:
        pooled = await self._ensure_session(server_id)
        if tool_name not in pooled.available_tools:
            raise MCPCallError(
                "Configured MCP tool was not advertised by the server.",
                outcome_may_be_indeterminate=False,
            )
        try:
            async with pooled.call_lock:
                return await asyncio.wait_for(
                    pooled.session.call_tool(tool_name, arguments=arguments),
                    timeout=timeout_seconds,
                )
        except asyncio.TimeoutError as exc:
            await self._drop_session(server_id)
            raise MCPCallTimeout(
                "MCP tool invocation timed out.",
                outcome_may_be_indeterminate=True,
            ) from exc
        except MCPCallError:
            raise
        except Exception as exc:
            await self._drop_session(server_id)
            raise MCPCallError(
                f"MCP session failed during tool invocation ({type(exc).__name__}).",
                outcome_may_be_indeterminate=True,
            ) from exc

    # Lazily start one official-SDK stdio session and verify advertised tools.
    async def _ensure_session(self, server_id: str) -> _PooledSession:
        existing = self._sessions.get(server_id)
        if existing is not None:
            return existing
        lock = self._session_locks.setdefault(server_id, asyncio.Lock())
        async with lock:
            existing = self._sessions.get(server_id)
            if existing is not None:
                return existing
            return await self._create_session(server_id)

    # Start one official-SDK stdio process after per-server creation is serialized.
    async def _create_session(self, server_id: str) -> _PooledSession:
        server = self._registry.mcp_servers[server_id]
        if server.transport != MCPTransportKind.STDIO:
            raise MCPCallError(
                "Configured MCP transport is unsupported.",
                outcome_may_be_indeterminate=False,
            )
        # Resolve allowlisted connector settings from the process or repository .env.
        environment = {
            key: value
            for key in server.environment_keys
            if (value := get_env_value(key)) is not None
        }
        parameters = StdioServerParameters(
            command=server.command,
            args=list(server.args),
            env=environment or None,
            cwd=server.working_directory,
        )
        stack = AsyncExitStack()
        try:
            null_log = open(os.devnull, "w", encoding="utf-8")
            stack.callback(null_log.close)
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(parameters, errlog=null_log)
            )
            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await asyncio.wait_for(session.initialize(), timeout=server.timeout_seconds)
            tools = await asyncio.wait_for(
                session.list_tools(),
                timeout=server.timeout_seconds,
            )
        except Exception as exc:
            await stack.aclose()
            raise MCPCallError(
                f"MCP session initialization failed ({type(exc).__name__}).",
                outcome_may_be_indeterminate=False,
            ) from exc
        pooled = _PooledSession(
            stack=stack,
            session=session,
            available_tools=frozenset(tool.name for tool in tools.tools),
            call_lock=asyncio.Lock(),
        )
        self._sessions[server_id] = pooled
        return pooled

    # Remove one interrupted session so a later independent request can reconnect.
    async def _drop_session(self, server_id: str) -> None:
        pooled = self._sessions.pop(server_id, None)
        if pooled is not None:
            try:
                await pooled.stack.aclose()
            except Exception:
                pass

    # Schedule best-effort cleanup after a synchronous timeout.
    def _schedule_drop(self, server_id: str) -> None:
        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._drop_session(server_id), self._loop)

    # Close every active server process before the pool loop stops.
    async def _close_all(self) -> None:
        for server_id in tuple(self._sessions):
            await self._drop_session(server_id)

    # Run the private event loop for the lifetime of this explicit pool instance.
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()
        self._loop.close()


__all__ = [
    "MCPCallError",
    "MCPCallTimeout",
    "MCPConnectionPool",
    "MCPPoolClosedError",
]
