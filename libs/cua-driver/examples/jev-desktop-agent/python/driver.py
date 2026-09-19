from __future__ import annotations

import os
import sys
import uuid
from contextlib import AsyncExitStack
from typing import Any, Mapping

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from contracts import Candidate, Element, Observation


def driver_child_environment() -> dict[str, str]:
    """Build the cua-driver child environment without enabling a screen reader."""
    env = os.environ.copy()
    if sys.platform.startswith("linux"):
        env.setdefault("CUA_DRIVER_RS_A11Y_ADVERTISE_MODE", "is_enabled_only")
    return env


class CuaMcpDriver:
    """Persistent Cua Driver MCP session for native desktop observations/actions."""

    def __init__(self, binary: str | None = None) -> None:
        self._binary = binary or os.getenv("CUA_DRIVER_BIN", "cua-driver")
        self._label = f"jev-desktop-{uuid.uuid4().hex[:8]}"
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "CuaMcpDriver":
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        try:
            params = StdioServerParameters(
                command=self._binary,
                args=["mcp"],
                env=driver_child_environment(),
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            await self._session.initialize()
            return self
        except BaseException:
            await self._stack.aclose()
            self._stack = None
            self._session = None
            raise

    async def __aexit__(self, exc_type, exc, tb) -> None:
        stack = self._stack
        self._stack = None
        self._session = None
        if stack is not None:
            await stack.__aexit__(exc_type, exc, tb)

    async def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            raise RuntimeError("CuaMcpDriver must be used as an async context manager")
        result = await self._session.call_tool(name, {**arguments, "session": self._label})
        if result.isError:
            raise RuntimeError(f"{name} failed: {result.content}")
        data = result.structuredContent
        if not isinstance(data, dict):
            raise RuntimeError(f"{name} returned no structured result")
        if data.get("status") == "refused" or data.get("refusal"):
            raise RuntimeError(f"{name} refused: {data.get('refusal', data)}")
        return data

    async def observe(self, app: str | None = None) -> Observation:
        listed = await self._call("list_windows", {})
        windows = list(listed.get("windows") or [])
        visible = [window for window in windows if window.get("is_on_screen", True)]
        if app:
            needle = app.casefold()
            visible = [
                window
                for window in visible
                if needle
                in f"{window.get('app_name', '')} {window.get('title', '')}".casefold()
            ]
        if not visible:
            if (
                sys.platform.startswith("linux")
                and not os.getenv("DISPLAY")
                and not os.getenv("WAYLAND_DISPLAY")
            ):
                raise RuntimeError(
                    "no visible windows: this shell has neither DISPLAY nor "
                    "WAYLAND_DISPLAY. Run the agent from a terminal inside the "
                    "graphical desktop session (or restore that session's display "
                    "environment) before controlling desktop apps."
                )
            message = f"no visible window matched {app!r}" if app else "no visible windows"
            raise RuntimeError(message)

        def area(window: dict[str, Any]) -> float:
            bounds = window.get("bounds") or {}
            return float(bounds.get("width", 0)) * float(bounds.get("height", 0))

        window = max(visible, key=area)
        pid = int(window["pid"])
        window_id = int(window["window_id"])
        state = await self._call(
            "get_window_state",
            {
                "pid": pid,
                "window_id": window_id,
                "include_screenshot": False,
                "max_elements": 1500,
            },
        )
        raw_elements = state.get("elements") or []
        elements = tuple(
            Element(
                index=int(raw.get("element_index", position)),
                token=(
                    raw.get("element_token")
                    if isinstance(raw.get("element_token"), str)
                    else None
                ),
                role=str(raw.get("role") or "Unknown"),
                label=str(raw.get("label") or ""),
                enabled=bool(raw.get("enabled", True)),
                value=str(raw["value"]) if raw.get("value") is not None else None,
            )
            for position, raw in enumerate(raw_elements)
        )
        return Observation(
            snapshot_id=str(state.get("snapshot_id") or "unknown"),
            pid=pid,
            window_id=window_id,
            app=str(state.get("app_name") or window.get("app_name") or "unknown"),
            window_title=str(state.get("window_title") or window.get("title") or ""),
            elements=elements,
            degraded=bool(state.get("degraded", False)),
            truncated=bool(state.get("truncated", False)),
        )

    async def execute(self, candidate: Candidate) -> Mapping[str, Any]:
        if candidate.tool is None:
            raise ValueError("terminal candidates are not executable")
        return await self._call(candidate.tool, dict(candidate.arguments))
