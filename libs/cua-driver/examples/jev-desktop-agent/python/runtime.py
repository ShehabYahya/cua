from __future__ import annotations

import asyncio
import contextlib
import os
import platform
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from contracts import Candidate, ConfirmationCallback
from driver import CuaMcpDriver
from events import EventSink, RuntimeEvent, RuntimeEventBus
from jev_adapter import chooser_from_env
from loop import AgentLoop, RunResult
from openrouter_client import (
    DEFAULT_REASONING_MODEL,
    DEFAULT_STT_MODEL,
    OpenRouterClient,
)
from perception import NoopPerceiver, OpenRouterVisionPerceiver
from voice import VoiceAssistant
from porter_voice import HandsFreeVoiceConfig, HandsFreeVoiceService
from writer import OpenRouterWriter


@dataclass(frozen=True)
class PorterRuntimeConfig:
    """Backend settings that are independent of any specific frontend."""

    provider: str = "auto"
    jev_model: str | None = None
    vision_enabled: bool = True
    vision_model: str = DEFAULT_REASONING_MODEL
    writer_model: str = DEFAULT_REASONING_MODEL
    max_steps: int = 30
    max_candidates: int = 32
    download_root: str | None = None
    enforce_policy: bool = False
    allow_foreground: bool = True
    visual_click_mode: str = "strict"

    def resolved_download_root(self) -> str | None:
        if self.download_root:
            return str(Path(self.download_root).expanduser())
        default = Path.home() / "Downloads"
        return str(default) if default.is_dir() else None


class PorterRuntime:
    """Long-lived owner of Cua, Jev, OpenRouter and AgentLoop.

    This is the application-facing backend boundary. CLI, Qt/QML, voice and
    future IPC frontends should submit commands here instead of constructing
    their own Driver/model stacks.
    """

    def __init__(
        self,
        config: PorterRuntimeConfig | None = None,
        *,
        event_sink: EventSink | None = None,
        chooser_factory: Callable[[str], Any] = chooser_from_env,
        driver_factory: Callable[[], Any] = CuaMcpDriver,
        openrouter_factory: Callable[[], OpenRouterClient] = OpenRouterClient,
        agent_factory: Callable[..., AgentLoop] = AgentLoop,
    ) -> None:
        self.config = config or PorterRuntimeConfig()
        self.events = RuntimeEventBus()
        if event_sink is not None:
            self.events.subscribe(event_sink)

        self._chooser_factory = chooser_factory
        self._driver_factory = driver_factory
        self._openrouter_factory = openrouter_factory
        self._agent_factory = agent_factory

        self._stack: contextlib.AsyncExitStack | None = None
        self._driver = None
        self._chooser = None
        self._openrouter: OpenRouterClient | None = None
        self._agent: AgentLoop | None = None
        self._voice_service: HandsFreeVoiceService | None = None

        self._command_lock = asyncio.Lock()
        self._cancel_event: asyncio.Event | None = None
        self._active_command_id: str | None = None
        self._started = False

    async def __aenter__(self) -> "PorterRuntime":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    @property
    def started(self) -> bool:
        return self._started

    @property
    def busy(self) -> bool:
        return self._command_lock.locked()

    @property
    def active_command_id(self) -> str | None:
        return self._active_command_id

    @property
    def agent(self) -> AgentLoop:
        if self._agent is None:
            raise RuntimeError("PorterRuntime has not been started")
        return self._agent

    @property
    def openrouter(self) -> OpenRouterClient | None:
        return self._openrouter

    def _emit(
        self,
        kind: str,
        message: str = "",
        *,
        command_id: str | None = None,
        **data: Any,
    ) -> None:
        self.events.emit(
            RuntimeEvent(
                kind=kind,
                message=message,
                command_id=command_id,
                data=data,
            )
        )

    def emit_event(
        self,
        kind: str,
        message: str = "",
        command_id: str | None = None,
        data: dict[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        """Public event hook for resident services such as hands-free voice."""
        payload = dict(data or {})
        payload.update(extra)
        self._emit(
            kind,
            message,
            command_id=command_id,
            **payload,
        )

    def _progress(self, message: str) -> None:
        self._emit(
            "agent_progress",
            message,
            command_id=self._active_command_id,
        )

    async def start(self) -> None:
        if self._started:
            return
        self._emit("runtime_starting", "Starting Porter runtime…")
        stack = contextlib.AsyncExitStack()
        await stack.__aenter__()
        try:
            chooser = self._chooser_factory(
                self.config.provider,
                model=self.config.jev_model,
            )
            close_chooser = getattr(chooser, "close", None)
            if callable(close_chooser):
                stack.callback(close_chooser)

            openrouter = None
            if os.getenv("OPENROUTER_API_KEY", "").strip():
                openrouter = self._openrouter_factory()
                close_openrouter = getattr(openrouter, "close", None)
                if callable(close_openrouter):
                    stack.callback(close_openrouter)

            perceiver = (
                OpenRouterVisionPerceiver(
                    openrouter,
                    model=self.config.vision_model,
                )
                if self.config.vision_enabled and openrouter is not None
                else NoopPerceiver()
            )
            writer = (
                OpenRouterWriter(
                    openrouter,
                    model=self.config.writer_model,
                )
                if openrouter is not None
                else None
            )

            driver = await stack.enter_async_context(self._driver_factory())
            agent = self._agent_factory(
                driver,
                chooser,
                writer=writer,
                perceiver=perceiver,
                max_steps=self.config.max_steps,
                max_candidates=self.config.max_candidates,
                download_root=self.config.resolved_download_root(),
                progress=self._progress,
                enforce_policy=self.config.enforce_policy,
                visual_click_mode=self.config.visual_click_mode,
            )
        except BaseException:
            await stack.aclose()
            self._emit("runtime_failed", "Porter runtime failed to start.")
            raise

        self._stack = stack
        self._driver = driver
        self._chooser = chooser
        self._openrouter = openrouter
        self._agent = agent
        self._started = True
        self._emit(
            "runtime_ready",
            "Porter is ready.",
            openrouter=bool(openrouter),
            provider=self.config.provider,
        )

    async def stop(self) -> None:
        if not self._started and self._stack is None:
            return
        self._emit("runtime_stopping", "Stopping Porter runtime…")

        await self.stop_hands_free()
        self.cancel()

        if self._command_lock.locked():
            async def wait_until_idle() -> None:
                while self._command_lock.locked():
                    await asyncio.sleep(0.05)

            try:
                await asyncio.wait_for(wait_until_idle(), timeout=5.0)
            except asyncio.TimeoutError:
                # An atomic provider/Driver call may not be cancellable mid-call.
                # After a bounded grace period, continue shutdown rather than
                # keeping the desktop application alive indefinitely.
                pass

        stack = self._stack
        self._stack = None
        self._driver = None
        self._chooser = None
        self._openrouter = None
        self._agent = None
        self._started = False
        self._active_command_id = None
        self._cancel_event = None
        if stack is not None:
            await stack.aclose()
        self._emit("runtime_stopped", "Porter stopped.")

    async def submit(
        self,
        goal: str,
        *,
        app: str | None = None,
        act: bool = True,
        approve_consequential: bool = False,
        allow_foreground: bool | None = None,
        confirm: ConfirmationCallback | None = None,
    ) -> RunResult:
        if not self._started or self._agent is None:
            raise RuntimeError("PorterRuntime must be started before submit()")
        command = goal.strip()
        if not command:
            raise ValueError("goal must not be empty")

        async with self._command_lock:
            command_id = uuid.uuid4().hex
            cancel_event = asyncio.Event()
            self._active_command_id = command_id
            self._cancel_event = cancel_event
            self._emit(
                "command_started",
                command,
                command_id=command_id,
                goal=command,
                app=app,
            )
            try:
                result = await self._agent.run(
                    command,
                    app=app,
                    act=act,
                    approve_consequential=approve_consequential,
                    allow_foreground=(
                        self.config.allow_foreground
                        if allow_foreground is None
                        else bool(allow_foreground)
                    ),
                    confirm=confirm,
                    cancel_event=cancel_event,
                )
            except Exception as error:
                self._emit(
                    "command_failed",
                    str(error),
                    command_id=command_id,
                    error_type=type(error).__name__,
                )
                raise
            else:
                self._emit(
                    "command_completed",
                    result.message,
                    command_id=command_id,
                    status=result.status,
                    executed_steps=sum(
                        1 for step in result.steps if step.executed
                    ),
                )
                return result
            finally:
                self._active_command_id = None
                self._cancel_event = None

    def cancel(self) -> bool:
        event = self._cancel_event
        command_id = self._active_command_id
        if event is None or event.is_set():
            return False
        event.set()
        self._emit(
            "command_cancel_requested",
            "Cancellation requested.",
            command_id=command_id,
        )
        return True

    @property
    def hands_free_enabled(self) -> bool:
        service = self._voice_service
        return bool(service is not None and service.running)

    async def start_hands_free(
        self,
        config: HandsFreeVoiceConfig | None = None,
    ) -> bool:
        if not self._started:
            raise RuntimeError(
                "PorterRuntime must be started before hands-free voice"
            )
        if self._openrouter is None:
            self._emit(
                "voice_unavailable",
                "Hands-free voice requires an OpenRouter API key for transcription.",
            )
            return False
        if self.hands_free_enabled:
            return True
        service = HandsFreeVoiceService(
            self,
            self._openrouter,
            config or HandsFreeVoiceConfig(),
        )
        self._voice_service = service
        try:
            await service.start()
        except Exception:
            self._voice_service = None
            raise
        return True

    async def stop_hands_free(self) -> None:
        service = self._voice_service
        if service is None:
            return
        self._voice_service = None
        await service.stop()

    async def set_hands_free(
        self,
        enabled: bool,
        config: HandsFreeVoiceConfig | None = None,
    ) -> bool:
        if enabled:
            return await self.start_hands_free(config)
        await self.stop_hands_free()
        return False

    def create_voice_assistant(
        self,
        *,
        stt_model: str = DEFAULT_STT_MODEL,
        language: str | None = None,
        speak: bool = False,
        microphone_device=None,
        allow_foreground: bool = False,
        silence_seconds: float = 0.5,
    ) -> VoiceAssistant:
        if not self._started:
            raise RuntimeError("PorterRuntime must be started before voice")
        if self._openrouter is None:
            raise RuntimeError(
                "voice mode requires OPENROUTER_API_KEY for transcription"
            )
        return VoiceAssistant(
            self.agent,
            self._openrouter,
            stt_model=stt_model,
            language=language,
            speak=speak,
            microphone_device=microphone_device,
            allow_foreground=allow_foreground,
            silence_seconds=silence_seconds,
        )

    async def diagnostics(self) -> dict[str, Any]:
        """Return explicit on-demand diagnostics for the native Advanced page."""
        if not self._started or self._driver is None:
            raise RuntimeError("Porter runtime is not started")

        warnings = await self._driver.health_warnings()
        limitations = self._driver.capability_limitations()
        overview = await self._driver.desktop_overview(
            include_screenshot=False,
            include_apps=True,
        )
        return {
            "status": "ok" if not warnings else "degraded",
            "warnings": list(warnings),
            "limitations": list(limitations),
            "capabilities": self._driver.capability_summary(),
            "visible_windows": len(overview.windows),
            "known_apps": len(overview.apps),
            "provider": self.config.provider,
            "jev_model": self.config.jev_model,
            "vision_enabled": self.config.vision_enabled,
            "hands_free": self.hands_free_enabled,
            "visual_click_mode": self.config.visual_click_mode,
            "policy_enabled": self.config.enforce_policy,
            "allow_foreground": self.config.allow_foreground,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        }

    @staticmethod
    async def preflight() -> dict[str, Any]:
        async with CuaMcpDriver() as driver:
            warnings = await driver.health_warnings()
            limitations = driver.capability_limitations()
            overview = await driver.desktop_overview()
            return {
                "status": "ok" if not warnings else "degraded",
                "warnings": list(warnings),
                "limitations": list(limitations),
                "capabilities": driver.capability_summary(),
                "visible_windows": len(overview.windows),
                "known_apps": len(overview.apps),
                "desktop_screenshot": bool(overview.screenshot_path),
            }
