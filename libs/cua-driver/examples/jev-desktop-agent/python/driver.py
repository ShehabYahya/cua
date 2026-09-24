from __future__ import annotations

import base64
import os
import sys
import tempfile
import uuid
from contextlib import AsyncExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from contracts import (
    Candidate,
    DesktopOverview,
    DriverRefusal,
    Element,
    Observation,
    Rect,
    VisualRegion,
    thaw,
)


def driver_child_environment() -> dict[str, str]:
    env = os.environ.copy()
    if sys.platform.startswith("linux"):
        # On GNOME, claiming ScreenReaderEnabled can start Orca. IsEnabled is
        # enough for the AT-SPI bridge without telling the desktop a screen
        # reader is active.
        env.setdefault("CUA_DRIVER_RS_A11Y_ADVERTISE_MODE", "is_enabled_only")
        if env.get("WAYLAND_DISPLAY"):
            env.setdefault("CUA_DRIVER_RS_ENABLE_WAYLAND", "1")
    return env


def _rect(raw: Any) -> Rect | None:
    if not isinstance(raw, Mapping):
        return None
    try:
        x = float(raw.get("x", 0))
        y = float(raw.get("y", 0))
        width = float(raw.get("width", raw.get("w", 0)))
        height = float(raw.get("height", raw.get("h", 0)))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return Rect(x, y, width, height)


def _screenshot_error(state: Mapping[str, Any]) -> str | None:
    raw = state.get("screenshot_error")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, Mapping):
        code = raw.get("code")
        message = raw.get("message") or raw.get("reason")
        return (
            ": ".join(str(value) for value in (code, message) if value)
            or str(dict(raw))
        )
    return None


def _result_text(result: Any) -> str | None:
    for part in getattr(result, "content", None) or []:
        if getattr(part, "type", None) != "text":
            continue
        text = getattr(part, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


# Browser semantic refs expose a `visibility` enum
# (cua-driver-core/src/browser/store.rs: BrowserVisibility). semantic.rs:749-776:
# NearViewport does NOT intersect the viewport, only its expanded margin.
def _browser_visible(raw_value: Any) -> bool | None:
    if not isinstance(raw_value, str):
        return None
    value = raw_value.casefold()
    if value == "in_viewport":
        return True
    if value in {"near_viewport", "offscreen", "css_hidden", "no_layout", "page_occluded"}:
        return False
    return None


def _bool_state(states: Any, key: str) -> bool | None:
    if not isinstance(states, Mapping):
        return None
    value = states.get(key)
    return value if isinstance(value, bool) else None


def _visual_regions(
    payload: Mapping[str, Any],
    *,
    capture_id: str,
) -> tuple[VisualRegion, ...]:
    if payload.get("schema") != "cua.visual_regions_v1":
        return ()
    capture = payload.get("capture")
    if not isinstance(capture, Mapping) or capture.get("capture_id") != capture_id:
        return ()
    out: list[VisualRegion] = []
    for index, raw in enumerate(payload.get("regions") or []):
        if not isinstance(raw, Mapping):
            continue
        bounds = _rect(raw.get("bounds"))
        if bounds is None:
            continue
        label = raw.get("label") or raw.get("text")
        if not isinstance(label, str) or not label.strip():
            continue
        try:
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        if not 0.0 <= confidence <= 1.0:
            continue
        out.append(
            VisualRegion(
                id=str(raw.get("id") or f"local-{index + 1}"),
                label=label.strip(),
                kind=str(raw.get("kind") or "control"),
                bounds=bounds,
                confidence=confidence,
                interactive=bool(raw.get("interactive", False)),
                source="cua-perception",
            )
        )
    return tuple(out)


_SESSION_START_TOOLS = ("call_start_session", "start_session")
_SESSION_END_TOOLS = ("call_end_session", "end_session")


class CuaMcpDriver:
    def __init__(self, binary: str | None = None) -> None:
        self._binary = binary or os.getenv("CUA_DRIVER_BIN", "cua-driver")
        self._label = f"jev-desktop-{uuid.uuid4().hex[:8]}"
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._tool_schemas: dict[str, dict[str, Any]] = {}
        self._temp_dir: str | None = None
        self.capture_bound_click = False
        self.coordinate_click_supported = False
        self._browser_route_unavailable: set[tuple[int, int]] = set()

    async def __aenter__(self) -> "CuaMcpDriver":
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        keep = os.getenv("JEV_DESKTOP_KEEP_ARTIFACTS", "").casefold() in {
            "1",
            "true",
            "yes",
        }
        if keep:
            self._temp_dir = tempfile.mkdtemp(prefix="cua-jev-desktop-")
        else:
            temp = tempfile.TemporaryDirectory(prefix="cua-jev-desktop-")
            self._temp_dir = temp.name
            self._stack.callback(temp.cleanup)
        try:
            params = StdioServerParameters(
                command=self._binary,
                args=["mcp"],
                env=driver_child_environment(),
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))
            self._session = await self._stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()
            tools = (await self._session.list_tools()).tools
            for tool in tools:
                schema = getattr(tool, "inputSchema", None)
                if not isinstance(schema, dict):
                    schema = getattr(tool, "input_schema", None)
                self._tool_schemas[str(tool.name)] = (
                    schema if isinstance(schema, dict) else {}
                )
            self.capture_bound_click = self.has_property("click", "capture_id")
            self.coordinate_click_supported = self._supports_coordinate_click()
            self._browser_route_unavailable.clear()
            await self._start_driver_session(require=True)
            return self
        except BaseException:
            await self._stack.aclose()
            self._stack = None
            self._session = None
            raise

    async def __aexit__(self, exc_type, exc, tb) -> None:
        stack = self._stack
        if self._session is not None:
            try:
                await self._end_driver_session()
            except Exception:
                pass
        self._browser_route_unavailable.clear()
        self._stack = None
        self._session = None
        if stack is not None:
            await stack.__aexit__(exc_type, exc, tb)

    def _session_tool(
        self,
        names: tuple[str, ...],
    ) -> str | None:
        return next(
            (name for name in names if self.has_tool(name)),
            None,
        )

    async def _start_driver_session(
        self,
        *,
        require: bool = False,
    ) -> None:
        name = self._session_tool(_SESSION_START_TOOLS)
        if name is None:
            if require:
                raise RuntimeError(
                    "Cua Driver does not advertise call_start_session "
                    "or start_session; update Cua Driver before using Porter"
                )
            return
        await self._call(name, {})

    async def _end_driver_session(self) -> None:
        name = self._session_tool(_SESSION_END_TOOLS)
        if name is not None:
            await self._call(name, {})

    async def revive_session(self) -> None:
        await self._start_driver_session(require=True)
        self._browser_route_unavailable.clear()

    def _supports_coordinate_click(self) -> bool:
        schema = self._tool_schemas.get("click") or {}
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return False
        if "x" not in properties or "y" not in properties:
            return False
        if "target" in properties:
            return True
        return "pid" in properties and "window_id" in properties

    def _prune_browser_route_cache(
        self,
        windows: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
    ) -> None:
        if not self._browser_route_unavailable:
            return
        present: set[tuple[int, int]] = set()
        for window in windows:
            if not isinstance(window, Mapping):
                continue
            pid = window.get("pid")
            window_id = window.get("window_id")
            if isinstance(pid, int) and isinstance(window_id, int):
                present.add((pid, window_id))
        self._browser_route_unavailable &= present

    def has_tool(self, name: str) -> bool:
        return name in self._tool_schemas

    def has_property(self, tool: str, prop: str) -> bool:
        schema = self._tool_schemas.get(tool) or {}
        properties = schema.get("properties")
        return isinstance(properties, dict) and prop in properties

    async def _call(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if self._session is None:
            raise RuntimeError(
                "CuaMcpDriver must be used as an async context manager"
            )
        payload = dict(arguments)
        if self.has_property(name, "session"):
            payload["session"] = self._label
        result = await self._session.call_tool(name, payload)
        data = result.structuredContent
        if isinstance(data, dict):
            refusal = data.get("refusal")
            refusal_map = refusal if isinstance(refusal, Mapping) else {}
            code = data.get("code")
            if not isinstance(code, str) or not code:
                nested_code = refusal_map.get("code")
                code = (
                    nested_code
                    if isinstance(nested_code, str) and nested_code
                    else None
                )
            refused = (
                data.get("status") == "refused"
                or data.get("effect") == "refused"
                or refusal is not None
                or (bool(result.isError) and isinstance(code, str) and bool(code))
            )
            if refused:
                detail = data.get("detail")
                detail_map = detail if isinstance(detail, Mapping) else {}
                nested_detail = refusal_map.get("detail")
                if not detail_map and isinstance(nested_detail, Mapping):
                    detail_map = nested_detail
                error = data.get("error")
                error_map = error if isinstance(error, Mapping) else {}

                reason_value: Any = None
                if isinstance(refusal, str) and refusal.strip():
                    reason_value = refusal.strip()
                for source in (refusal_map, detail_map, error_map):
                    if reason_value:
                        break
                    reason_value = source.get("reason")
                for source in (refusal_map, detail_map, error_map):
                    if reason_value:
                        break
                    reason_value = source.get("message")
                if not reason_value:
                    reason_value = (
                        code
                        or data.get("message")
                        or _result_text(result)
                    )
                if not reason_value:
                    reason_value = data

                escalation: Mapping[str, Any] | None = None
                for candidate_map in (
                    data.get("escalation"),
                    refusal_map.get("escalation"),
                    detail_map.get("escalation"),
                ):
                    if isinstance(candidate_map, Mapping):
                        escalation = candidate_map
                        break
                recommended = None
                if escalation is not None:
                    candidate_target = (
                        escalation.get("target")
                        or escalation.get("recommended")
                    )
                    if isinstance(candidate_target, str) and candidate_target:
                        recommended = candidate_target
                raise DriverRefusal(
                    name,
                    str(reason_value),
                    code=str(code) if isinstance(code, str) else None,
                    recommended=(
                        str(recommended)
                        if recommended
                        else None
                    ),
                )
        if result.isError:
            reason = _result_text(result) or str(result.content)
            if "tool_invocation_failed" in reason.casefold():
                raise DriverRefusal(
                    name,
                    (
                        "Cua Driver could not invoke the tool. Porter will "
                        "restart the Driver session and retry."
                    ),
                    code="tool_invocation_failed",
                )
            raise RuntimeError(f"{name} failed: {reason}")
        if not isinstance(data, dict):
            raise RuntimeError(f"{name} returned no structured result")

        inline_images: list[dict[str, str]] = []
        for part in result.content or []:
            if getattr(part, "type", None) != "image":
                continue
            encoded = getattr(part, "data", None)
            mime = (
                getattr(part, "mimeType", None)
                or getattr(part, "mime_type", None)
            )
            if (
                isinstance(encoded, str)
                and isinstance(mime, str)
                and encoded
            ):
                inline_images.append(
                    {"data": encoded, "mime_type": mime}
                )
        if inline_images:
            data = dict(data)
            data["__inline_images"] = inline_images
        return data

    def capability_summary(self) -> dict[str, Any]:
        names = set(self._tool_schemas)
        return {
            "native_observation": {
                "list_windows": "list_windows" in names,
                "get_window_state": "get_window_state" in names,
                "get_desktop_state": "get_desktop_state" in names,
            },
            "native_actions": {
                name: name in names
                for name in (
                    "click",
                    "double_click",
                    "right_click",
                    "type_text",
                    "press_key",
                    "hotkey",
                    "scroll",
                    "drag",
                )
            },
            "browser": {
                name: name in names
                for name in (
                    "get_browser_state",
                    "browser_click",
                    "browser_type",
                    "browser_pointer",
                    "browser_download",
                    "browser_set_input_files",
                    "browser_navigate",
                )
            },
            "perception": {
                "parse_visual_regions": "parse_visual_regions" in names,
                "capture_bound_click": self.capture_bound_click,
            },
            "input": {
                "coordinate_click_supported": self.coordinate_click_supported,
            },
            "health_report": "health_report" in names,
        }

    async def health_warnings(self) -> tuple[str, ...]:
        warnings: list[str] = []
        if not self.has_tool("health_report"):
            warnings.append(
                "This Cua Driver does not advertise health_report; "
                "upgrade to the latest Driver before relying on full "
                "visual/Wayland behavior."
            )
        else:
            try:
                report = await self._call("health_report", {})
                overall = report.get("overall")
                if isinstance(overall, str) and overall != "ok":
                    warnings.append(
                        f"Cua Driver health is {overall}."
                    )
                checks = report.get("checks")
                if isinstance(checks, list):
                    for check in checks:
                        if not isinstance(check, Mapping):
                            continue
                        if check.get("status") != "fail":
                            continue
                        name = str(check.get("name") or "check")
                        message = str(check.get("message") or "failed")
                        hint = check.get("hint")
                        detail = f"{name}: {message}"
                        if isinstance(hint, str) and hint.strip():
                            detail += f" — {hint.strip()}"
                        warnings.append(detail)
            except Exception as error:
                warnings.append(
                    f"Cua Driver health_report could not be read: {error}"
                )

        return tuple(warnings)

    def capability_limitations(self) -> tuple[str, ...]:
        limitations: list[str] = []
        if not self.has_tool("parse_visual_regions"):
            limitations.append(
                "Driver does not advertise parse_visual_regions; the agent "
                "will use semantic accessibility/browser state first and may "
                "use OpenRouter vision for screenshot interpretation."
            )
        if not self.capture_bound_click:
            limitations.append(
                "Driver does not advertise capture-bound raw pixel clicks. "
                "Visual-only controls can be understood from screenshots, but "
                "the agent will not execute unbound vision coordinates. "
                "Semantic AX actions and typed browser refs remain enabled."
            )
        return tuple(limitations)

    def _materialize_inline_image(
        self,
        state: Mapping[str, Any],
        *,
        stem: str,
    ) -> str | None:
        if not self._temp_dir:
            return None
        raw_images = state.get("__inline_images")
        if not isinstance(raw_images, list) or not raw_images:
            return None
        first = raw_images[0]
        if not isinstance(first, Mapping):
            return None
        encoded = first.get("data")
        mime = str(first.get("mime_type") or "")
        if not isinstance(encoded, str) or not encoded:
            return None
        suffix = ".jpg" if "jpeg" in mime.casefold() else ".png"
        try:
            payload = base64.b64decode(encoded, validate=True)
        except Exception:
            return None
        if not payload or len(payload) > 25 * 1024 * 1024:
            return None
        path = Path(self._temp_dir) / (
            f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"
        )
        try:
            path.write_bytes(payload)
        except OSError:
            return None
        return str(path)

    async def list_windows(self) -> list[dict[str, Any]]:
        args: dict[str, Any] = {}
        if self.has_property("list_windows", "on_screen_only"):
            args["on_screen_only"] = True
        data = await self._call("list_windows", args)
        return list(data.get("windows") or [])

    async def list_apps(self) -> list[dict[str, Any]]:
        data = await self._call("list_apps", {})
        return list(data.get("apps") or [])

    @staticmethod
    def _window_matches(window: Mapping[str, Any], app: str) -> bool:
        needle = app.casefold().strip()
        hay = (
            f"{window.get('app_name', '')} {window.get('title', '')}"
            .casefold()
        )
        return needle in hay

    async def has_window(self, app: str) -> bool:
        return any(
            self._window_matches(window, app)
            for window in await self.list_windows()
        )

    async def ensure_app(self, app: str) -> None:
        if await self.has_window(app):
            return
        apps = await self.list_apps()
        needle = app.casefold().strip()
        ranked = sorted(
            apps,
            key=lambda item: (
                str(item.get("name") or "").casefold() == needle,
                needle in str(item.get("name") or "").casefold(),
                bool(item.get("running")),
            ),
            reverse=True,
        )
        match = next(
            (
                item
                for item in ranked
                if needle
                in f"{item.get('name', '')} {item.get('bundle_id', '')}".casefold()
            ),
            None,
        )
        if match is None:
            if not self.has_property("launch_app", "name"):
                raise RuntimeError(
                    f"Driver cannot launch {app!r} by name on this version"
                )
            await self._call("launch_app", {"name": app})
        else:
            launch_path = match.get("launch_path")
            bundle_id = match.get("bundle_id")
            name = str(match.get("name") or app)
            if (
                isinstance(launch_path, str)
                and launch_path
                and self.has_property("launch_app", "launch_path")
            ):
                args = {"launch_path": launch_path}
            elif (
                isinstance(bundle_id, str)
                and bundle_id
                and self.has_property("launch_app", "bundle_id")
            ):
                args = {"bundle_id": bundle_id}
            elif self.has_property("launch_app", "name"):
                args = {"name": name}
            else:
                raise RuntimeError(
                    f"Driver cannot launch {app!r} with its advertised schema"
                )
            await self._call("launch_app", args)
        for _ in range(32):
            if await self.has_window(app):
                return
            import asyncio

            await asyncio.sleep(0.25)
        raise RuntimeError(
            f"launched {app!r}, but no matching window became visible"
        )

    async def desktop_overview(
        self,
        *,
        include_screenshot: bool = True,
        include_apps: bool = True,
    ) -> DesktopOverview:
        windows = tuple(await self.list_windows())
        apps = tuple(await self.list_apps()) if include_apps else ()
        screenshot_path = None
        if (
            include_screenshot
            and self.has_tool("get_desktop_state")
            and self._temp_dir
            and self.has_property("get_desktop_state", "screenshot_out_file")
        ):
            proposed = str(Path(self._temp_dir) / "desktop.png")
            try:
                state = await self._call(
                    "get_desktop_state",
                    {"screenshot_out_file": proposed},
                )
                candidate = state.get("screenshot_file_path") or proposed
                if isinstance(candidate, str) and Path(candidate).is_file():
                    screenshot_path = candidate
                if screenshot_path is None:
                    screenshot_path = self._materialize_inline_image(
                        state,
                        stem="desktop",
                    )
            except Exception:
                screenshot_path = None
        return DesktopOverview(
            windows=windows,
            apps=apps,
            screenshot_path=screenshot_path,
        )

    def _choose_window(
        self,
        windows: list[dict[str, Any]],
        app: str | None,
    ) -> dict[str, Any]:
        visible = [
            window
            for window in windows
            if window.get("is_on_screen", True)
        ]
        if app:
            matched = [
                window
                for window in visible
                if self._window_matches(window, app)
            ]
            if not matched:
                seen = [
                    f"{window.get('app_name') or '?'} :: "
                    f"{window.get('title') or '?'}"
                    for window in visible[:12]
                ]
                raise RuntimeError(
                    f"no visible window matched {app!r}; Driver reported: "
                    + ("; ".join(seen) if seen else "<none>")
                )
            visible = matched
        if not visible:
            raise RuntimeError("Cua Driver reported no visible windows")
        with_z = [
            window
            for window in visible
            if isinstance(window.get("z_index"), int)
        ]
        if with_z:
            return max(with_z, key=lambda window: int(window["z_index"]))

        def area(window: Mapping[str, Any]) -> float:
            bounds = window.get("bounds") or {}
            return (
                float(bounds.get("width", 0))
                * float(bounds.get("height", 0))
            )

        return max(visible, key=area)

    async def _browser_semantic_state(
        self,
        pid: int,
        window_id: int,
    ) -> tuple[
        tuple[Element, ...],
        str | None,
        str | None,
        str | None,
        str | None,
    ]:
        if not self.has_tool("get_browser_state"):
            return (), None, None, None, None
        if (pid, window_id) in self._browser_route_unavailable:
            return (), None, None, None, None
        try:
            bind = await self._call(
                "get_browser_state",
                {"pid": pid, "window_id": window_id},
            )
        except DriverRefusal as error:
            if error.code == "session_ended":
                raise
            if error.code == "browser_route_unavailable":
                self._browser_route_unavailable.add((pid, window_id))
            return (), None, None, None, None
        except Exception:
            return (), None, None, None, None
        if (
            bind.get("status") != "ok"
            or bind.get("binding_quality") != "exact"
            or bind.get("mutation_allowed") is not True
        ):
            return (), None, None, None, None
        target_id = bind.get("target_id")
        tabs = bind.get("tabs")
        if not isinstance(target_id, str) or not isinstance(tabs, list):
            return (), None, None, None, None
        active = [
            tab
            for tab in tabs
            if isinstance(tab, Mapping)
            and tab.get("active") is True
            and isinstance(tab.get("tab_id"), str)
        ]
        if len(active) == 1:
            tab_id = str(active[0]["tab_id"])
        elif len(tabs) == 1 and isinstance(tabs[0], Mapping) and isinstance(
            tabs[0].get("tab_id"), str
        ):
            tab_id = str(tabs[0]["tab_id"])
        else:
            return (), None, None, None, None
        try:
            snapshot = await self._call(
                "get_browser_state",
                {
                    "target_id": target_id,
                    "tab_id": tab_id,
                    "snapshot_format": "semantic_v2",
                },
            )
        except DriverRefusal as error:
            if error.code == "session_ended":
                raise
            if error.code == "browser_route_unavailable":
                self._browser_route_unavailable.add((pid, window_id))
            return (), None, None, None, None
        except Exception:
            return (), None, None, None, None
        if snapshot.get("status") != "ok":
            return (), None, None, None, None
        refs = snapshot.get("refs")
        browser_elements: list[Element] = []
        if isinstance(refs, list):
            for offset, raw in enumerate(refs):
                if not isinstance(raw, Mapping):
                    continue
                ref = raw.get("ref")
                if not isinstance(ref, str) or not ref:
                    continue
                states = raw.get("states")
                disabled = (
                    states.get("disabled") is True
                    if isinstance(states, Mapping)
                    else False
                )
                actions_raw = raw.get("actions") or []
                actions = tuple(
                    str(action)
                    for action in actions_raw
                    if isinstance(action, str)
                )
                browser_elements.append(
                    Element(
                        index=100_000 + offset,
                        token=None,
                        role=str(raw.get("role") or "browser-control"),
                        label=str(raw.get("name") or ""),
                        enabled=not disabled,
                        value=(
                            str(raw["value"])
                            if raw.get("value") is not None
                            else None
                        ),
                        selected=_bool_state(states, "selected"),
                        actions=actions,
                        bounds=None,
                        source="browser",
                        browser_ref=ref,
                        focused=_bool_state(states, "focused"),
                        visible=_browser_visible(raw.get("visibility")),
                        expanded=_bool_state(states, "expanded"),
                        checked=_bool_state(states, "checked"),
                    )
                )
        page = snapshot.get("page")
        url = (
            str(page.get("url"))
            if isinstance(page, Mapping) and page.get("url")
            else None
        )
        title = (
            str(page.get("title"))
            if isinstance(page, Mapping) and page.get("title")
            else None
        )
        return (
            tuple(browser_elements),
            target_id,
            tab_id,
            url,
            title,
        )

    async def observe(
        self,
        app: str | None = None,
        *,
        include_screenshot: bool = True,
        windows: tuple[Mapping[str, Any], ...] | None = None,
        target_window: tuple[int, int] | None = None,
    ) -> Observation:
        inventory = (
            tuple(windows)
            if windows is not None
            else tuple(await self.list_windows())
        )
        self._prune_browser_route_cache(inventory)
        if target_window is not None:
            wanted_pid = int(target_window[0])
            wanted_window_id = int(target_window[1])
            window = next(
                (
                    candidate
                    for candidate in inventory
                    if candidate.get("pid") == wanted_pid
                    and candidate.get("window_id") == wanted_window_id
                ),
                None,
            )
            if window is None:
                raise RuntimeError(
                    "target window pid="
                    f"{wanted_pid} window_id={wanted_window_id} is no longer "
                    "present in the current window inventory; re-observe before "
                    "acting instead of substituting another window"
                )
        else:
            window = self._choose_window(list(inventory), app)
        pid = int(window["pid"])
        window_id = int(window["window_id"])
        args: dict[str, Any] = {
            "pid": pid,
            "window_id": window_id,
            "include_accessibility_tree": True,
            "include_screenshot": include_screenshot,
            "max_elements": 2500,
        }
        if include_screenshot:
            args["max_dimension"] = 1800
        proposed = None
        if (
            include_screenshot
            and self._temp_dir
            and self.has_property(
                "get_window_state",
                "screenshot_out_file",
            )
        ):
            proposed = str(
                Path(self._temp_dir)
                / f"window-{pid}-{window_id}-{uuid.uuid4().hex[:8]}.png"
            )
            args["screenshot_out_file"] = proposed
        schema_props = (
            (self._tool_schemas.get("get_window_state") or {})
            .get("properties")
        )
        if isinstance(schema_props, dict):
            args = {
                key: value
                for key, value in args.items()
                if key in schema_props
            }
            args.setdefault("pid", pid)
            args.setdefault("window_id", window_id)
        state = await self._call("get_window_state", args)
        raw_elements = state.get("elements") or []
        elements: list[Element] = []
        for position, raw in enumerate(raw_elements):
            if not isinstance(raw, Mapping):
                continue
            actions_raw = raw.get("actions") or []
            actions = tuple(
                str(value)
                for value in actions_raw
                if isinstance(value, str)
            )
            elements.append(
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
                    value=(
                        str(raw["value"])
                        if raw.get("value") is not None
                        else None
                    ),
                    selected=(
                        raw.get("selected")
                        if isinstance(raw.get("selected"), bool)
                        else None
                    ),
                    actions=actions,
                    bounds=_rect(raw.get("frame")),
                    parent_index=(
                        int(raw["parent_index"])
                        if isinstance(raw.get("parent_index"), int)
                        else None
                    ),
                    depth=(
                        int(raw["depth"])
                        if isinstance(raw.get("depth"), int)
                        else None
                    ),
                    in_web_content=(
                        raw.get("in_web_content")
                        if isinstance(raw.get("in_web_content"), bool)
                        else None
                    ),
                    # The native WindowElement contract exposes no
                    # focused/visible/expanded/checked field, so they stay None
                    # rather than being inferred from `selected`.
                )
            )
        (
            browser_elements,
            browser_target_id,
            browser_tab_id,
            browser_url,
            browser_title,
        ) = await self._browser_semantic_state(pid, window_id)
        if browser_elements:
            semantic_keys = {
                (item.role.casefold(), item.label.casefold())
                for item in browser_elements
                if item.label
            }
            elements = [
                item
                for item in elements
                if (
                    item.role.casefold(),
                    item.label.casefold(),
                )
                not in semantic_keys
            ]
            elements.extend(browser_elements)

        screenshot_path = state.get("screenshot_file_path")
        if (
            not isinstance(screenshot_path, str)
            or not Path(screenshot_path).is_file()
        ):
            screenshot_path = (
                proposed
                if proposed and Path(proposed).is_file()
                else None
            )
        if include_screenshot and screenshot_path is None:
            screenshot_path = self._materialize_inline_image(
                state,
                stem=f"window-{pid}-{window_id}",
            )
        if not include_screenshot:
            screenshot_path = None
        capture_id = (
            state.get("capture_id")
            if isinstance(state.get("capture_id"), str)
            else None
        )
        visual_regions: tuple[VisualRegion, ...] = ()
        if (
            capture_id
            and self.capture_bound_click
            and self.has_tool("parse_visual_regions")
        ):
            try:
                parsed = await self._call(
                    "parse_visual_regions",
                    {
                        "capture_id": capture_id,
                        "options": {
                            "kinds": ["text", "icon"],
                            "min_confidence": 0.55,
                            "max_regions": 100,
                        },
                    },
                )
                visual_regions = _visual_regions(
                    parsed,
                    capture_id=capture_id,
                )
            except Exception:
                visual_regions = ()
        return Observation(
            snapshot_id=str(state.get("snapshot_id") or "unknown"),
            pid=pid,
            window_id=window_id,
            app=str(
                state.get("app_name")
                or window.get("app_name")
                or "unknown"
            ),
            window_title=str(
                state.get("window_title")
                or window.get("title")
                or ""
            ),
            elements=tuple(elements),
            visual_regions=visual_regions,
            capture_id=capture_id,
            screenshot_path=screenshot_path,
            screenshot_width=(
                int(state["screenshot_width"])
                if isinstance(state.get("screenshot_width"), int)
                else None
            ),
            screenshot_height=(
                int(state["screenshot_height"])
                if isinstance(state.get("screenshot_height"), int)
                else None
            ),
            screenshot_error=_screenshot_error(state),
            degraded=bool(state.get("degraded", False)),
            truncated=bool(state.get("truncated", False)),
            browser_target_id=browser_target_id,
            browser_tab_id=browser_tab_id,
            browser_url=browser_url,
            browser_title=browser_title,
            desktop_windows=inventory,
            screenshot_frame_valid=(
                state.get("screenshot_frame_valid")
                if isinstance(state.get("screenshot_frame_valid"), bool)
                else None
            ),
        )

    def _compatible_arguments(
        self,
        tool: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        schema = self._tool_schemas.get(tool) or {}
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return arguments

        result = dict(arguments)
        target = result.get("target")
        if isinstance(target, Mapping) and "target" not in properties:
            result.pop("target", None)
            if target.get("kind") == "window":
                if "pid" in properties and target.get("pid") is not None:
                    result.setdefault("pid", target["pid"])
                if (
                    "window_id" in properties
                    and target.get("window_id") is not None
                ):
                    result.setdefault("window_id", target["window_id"])
            elif target.get("kind") == "desktop" and "scope" in properties:
                result.setdefault("scope", "desktop")

        # Candidates may carry both modern tokens and legacy snapshot/index
        # addresses. Keep only fields this exact Driver advertises.
        result = {
            key: value
            for key, value in result.items()
            if key in properties
        }
        return result

    async def execute(
        self,
        candidate: Candidate,
    ) -> Mapping[str, Any]:
        if candidate.tool is None:
            raise ValueError("terminal candidates are not executable")
        arguments = self._compatible_arguments(
            candidate.tool,
            thaw(candidate.arguments),
        )
        return await self._call(candidate.tool, arguments)

    def with_foreground(self, candidate: Candidate) -> Candidate:
        if candidate.tool is None:
            return candidate
        arguments = thaw(candidate.arguments)
        arguments["delivery_mode"] = "foreground"
        return replace(candidate, arguments=arguments)
