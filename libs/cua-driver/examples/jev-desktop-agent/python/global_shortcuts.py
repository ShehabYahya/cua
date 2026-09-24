from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from typing import Callable

from dbus_next import Message, MessageType, Variant
from dbus_next.aio import MessageBus

from version import APP_ID


PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
GLOBAL_SHORTCUTS_IFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"
REGISTRY_IFACE = "org.freedesktop.host.portal.Registry"

SHORTCUT_ID = "toggle-quick-bar"


@dataclass(frozen=True)
class GlobalShortcutConfig:
    enabled: bool = True
    preferred_trigger: str = "CTRL+ALT+space"


class GlobalShortcutPortal:
    """Best-effort Wayland-safe global shortcut through XDG Desktop Portal."""

    def __init__(
        self,
        config: GlobalShortcutConfig | None = None,
        *,
        on_activated: Callable[[], None],
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config or GlobalShortcutConfig()
        self._on_activated = on_activated
        self._on_status = on_status
        self._bus: MessageBus | None = None
        self._portal = None
        self._session_handle: str | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def _status(self, message: str) -> None:
        if self._on_status is None:
            return
        try:
            self._on_status(message)
        except Exception:
            pass

    @staticmethod
    def _unwrap(value):
        if isinstance(value, Variant):
            return GlobalShortcutPortal._unwrap(value.value)
        if isinstance(value, dict):
            return {
                key: GlobalShortcutPortal._unwrap(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return type(value)(
                GlobalShortcutPortal._unwrap(item) for item in value
            )
        return value

    async def _wait_request(self, token: str, invoke) -> dict:
        bus = self._bus
        if bus is None or not bus.unique_name:
            raise RuntimeError("portal bus is not connected")

        sender = bus.unique_name[1:].replace(".", "_")
        expected_path = f"{PORTAL_PATH}/request/{sender}/{token}"
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def handler(message: Message) -> bool:
            if (
                message.message_type == MessageType.SIGNAL
                and message.path == expected_path
                and message.interface == REQUEST_IFACE
                and message.member == "Response"
            ):
                if not future.done():
                    code, results = message.body
                    future.set_result(
                        (int(code), self._unwrap(results))
                    )
                return True
            return False

        bus.add_message_handler(handler)
        try:
            returned_handle = await invoke()
            # The returned handle should match the handle_token-derived path.
            # If a backend rewrites it, continue waiting for the predicted
            # portal request path, which is the stable contract for tokens.
            _ = returned_handle
            code, results = await asyncio.wait_for(future, timeout=30.0)
        finally:
            bus.remove_message_handler(handler)

        if code != 0:
            raise RuntimeError(f"portal request was declined ({code})")
        return dict(results or {})

    async def start(self) -> None:
        if self._running or not self.config.enabled:
            return

        self._status("Registering global shortcut…")
        bus = await MessageBus().connect()
        self._bus = bus

        try:
            introspection = await bus.introspect(PORTAL_NAME, PORTAL_PATH)
            portal = bus.get_proxy_object(
                PORTAL_NAME,
                PORTAL_PATH,
                introspection,
            )
            self._portal = portal

            # xdg-desktop-portal >= 1.20 can require host applications to
            # explicitly register their reverse-DNS desktop identity.
            try:
                registry = portal.get_interface(REGISTRY_IFACE)
                await registry.call_register(APP_ID, {})
            except Exception:
                # Older portal versions do not expose Registry.Register.
                pass

            shortcuts = portal.get_interface(GLOBAL_SHORTCUTS_IFACE)
            shortcuts.on_activated(self._activated)

            create_token = "porter_create_" + secrets.token_hex(6)
            session_token = "porter_session_" + secrets.token_hex(6)
            create_results = await self._wait_request(
                create_token,
                lambda: shortcuts.call_create_session(
                    {
                        "handle_token": Variant("s", create_token),
                        "session_handle_token": Variant("s", session_token),
                    }
                ),
            )

            session_handle = create_results.get("session_handle")
            if not isinstance(session_handle, str) or not session_handle:
                raise RuntimeError(
                    "global shortcut portal returned no session handle"
                )
            self._session_handle = session_handle

            bind_token = "porter_bind_" + secrets.token_hex(6)
            bind_results = await self._wait_request(
                bind_token,
                lambda: shortcuts.call_bind_shortcuts(
                    session_handle,
                    [
                        [
                            SHORTCUT_ID,
                            {
                                "description": Variant(
                                    "s",
                                    "Show or hide the Porter quick command bar",
                                ),
                                "preferred_trigger": Variant(
                                    "s",
                                    self.config.preferred_trigger,
                                ),
                            },
                        ]
                    ],
                    "",
                    {
                        "handle_token": Variant("s", bind_token),
                    },
                ),
            )

            bound = bind_results.get("shortcuts") or []
            trigger_description = ""
            for shortcut in bound:
                try:
                    shortcut_id, metadata = shortcut
                except Exception:
                    continue
                if shortcut_id != SHORTCUT_ID:
                    continue
                metadata = self._unwrap(metadata)
                trigger_description = str(
                    metadata.get("trigger_description") or ""
                )
                break

            self._running = True
            self._status(
                "Global shortcut ready"
                + (
                    f" — {trigger_description}"
                    if trigger_description
                    else ""
                )
            )
        except Exception:
            await self.stop()
            raise

    def _activated(
        self,
        session_handle: str,
        shortcut_id: str,
        timestamp: int,
        options,
    ) -> None:
        _ = (timestamp, options)
        if (
            self._running
            and shortcut_id == SHORTCUT_ID
            and session_handle == self._session_handle
        ):
            try:
                self._on_activated()
            except Exception:
                pass

    async def stop(self) -> None:
        bus = self._bus
        session_handle = self._session_handle
        self._running = False
        self._session_handle = None
        self._portal = None

        if bus is not None and session_handle:
            try:
                introspection = await bus.introspect(
                    PORTAL_NAME,
                    session_handle,
                )
                proxy = bus.get_proxy_object(
                    PORTAL_NAME,
                    session_handle,
                    introspection,
                )
                session = proxy.get_interface(SESSION_IFACE)
                await session.call_close()
            except Exception:
                pass

        if bus is not None:
            try:
                bus.disconnect()
            except Exception:
                pass

        self._bus = None
        self._status("Global shortcut inactive")
