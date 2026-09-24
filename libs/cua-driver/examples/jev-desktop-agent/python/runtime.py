from __future__ import annotations

import asyncio
import contextlib
import os
import platform
import sys
import threading
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
from porter_voice import (
    HandsFreeVoiceConfig,
    HandsFreeVoiceService,
    VoiceCaptureEngine,
)
from writer import OpenRouterWriter
from telemetry import Telemetry, TelemetryConfig
from telemetry_agent import TelemetryAgentLoop


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
    telemetry: TelemetryConfig | None = None

    def resolved_download_root(self) -> str | None:
        if self.download_root:
            return str(Path(self.download_root).expanduser())
        default = Path.home() / "Downloads"
        return str(default) if default.is_dir() else None


class PorterBusyError(RuntimeError):
    """Raised when a command or exclusive voice action is already active."""


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
        self._one_shot_voice_task: asyncio.Task | None = None
        self._one_shot_stop_event = None
        self._voice_config = HandsFreeVoiceConfig()

        self._command_lock = asyncio.Lock()
        self._cancel_event: asyncio.Event | None = None
        self._active_command_id: str | None = None
        self._cancelling = False
        self._started = False
        self._telemetry = Telemetry()

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
        return (
            self._active_command_id is not None
            or self._command_lock.locked()
        )

    @property
    def active_command_id(self) -> str | None:
        return self._active_command_id

    @property
    def cancelling(self) -> bool:
        return self._cancelling

    @property
    def one_shot_voice_active(self) -> bool:
        task = self._one_shot_voice_task
        return bool(task is not None and not task.done())

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

    @property
    def telemetry_status(self) -> dict[str, Any]:
        return self._telemetry.status()

    def _capture(self, method: str, *args, **kwargs) -> None:
        # A telemetry fault must not interrupt a command or mask its exception.
        try:
            getattr(self._telemetry, method)(*args, **kwargs)
        except Exception:
            self._telemetry.invalidate()

    async def _close_telemetry(self, telemetry: Telemetry) -> None:
        await asyncio.to_thread(telemetry.close)

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
            try:
                telemetry_config = self.config.telemetry or TelemetryConfig.from_env()
            except (ValueError, TypeError, OSError):
                telemetry_config = TelemetryConfig()
                self._emit("telemetry_disabled", "Invalid telemetry configuration; capture is off.")
            self._telemetry = Telemetry(telemetry_config)
            self._telemetry.start()
            stack.push_async_callback(self._close_telemetry, self._telemetry)
            agent_factory = self._agent_factory
            telemetry_args: dict[str, Any] = {}
            if self._telemetry.enabled and agent_factory is AgentLoop:
                agent_factory = TelemetryAgentLoop
                telemetry_args["telemetry"] = self._telemetry
            elif self._telemetry.enabled:
                # Custom agent factories need their own hooks; never imply a
                # lifecycle-only capture is a complete decision trace.
                self._telemetry.invalidate()
            agent = agent_factory(
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
                **telemetry_args,
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

        self.cancel()
        await self.cancel_listen_once()
        await self.stop_hands_free()

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
        self._cancelling = False
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
        input_source: str = "text",
        utterance_id: str | None = None,
    ) -> RunResult:
        if not self._started or self._agent is None:
            raise RuntimeError("PorterRuntime must be started before submit()")
        command = goal.strip()
        if not command:
            raise ValueError("goal must not be empty")
        if (
            self._command_lock.locked()
            or self._active_command_id is not None
            or self._cancelling
        ):
            raise PorterBusyError("Porter is already working.")

        command_id = uuid.uuid4().hex
        cancel_event = asyncio.Event()
        terminal_kind = "command_failed"
        terminal_message = ""
        terminal_data: dict[str, Any] = {}
        result: RunResult | None = None
        raised: BaseException | None = None

        # Reserve ownership before the first await. On the single runtime
        # event loop this makes concurrent submit() calls fail immediately
        # rather than waiting behind the command lock.
        self._active_command_id = command_id
        self._cancel_event = cancel_event
        self._cancelling = False
        try:
            await self._command_lock.acquire()
        except BaseException:
            self._active_command_id = None
            self._cancel_event = None
            self._cancelling = False
            raise
        self._capture(
            "begin_command",
            command_id,
            command,
            source=input_source,
            utterance_id=utterance_id,
        )
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
            if cancel_event.is_set():
                terminal_kind = "command_completed"
                terminal_message = "Command cancelled."
                terminal_data = {"status": "cancelled"}
                result = RunResult(
                    "cancelled",
                    result.steps,
                    terminal_message,
                    result.plan,
                    result.completed_subgoals,
                )
            else:
                terminal_kind = "command_completed"
                terminal_message = result.message
                terminal_data = {
                    "status": result.status,
                    "executed_steps": sum(
                        1 for step in result.steps if step.executed
                    ),
                }
        except asyncio.CancelledError as error:
            terminal_kind = "command_completed"
            terminal_message = "Command cancelled."
            terminal_data = {"status": "cancelled"}
            raised = error
        except Exception as error:
            terminal_kind = "command_failed"
            terminal_message = str(error)
            terminal_data = {"error_type": type(error).__name__}
            raised = error
        finally:
            status = str(terminal_data.get("status") or "failed")
            self._capture("end_command", status)
            self._active_command_id = None
            self._cancel_event = None
            self._cancelling = False
            if self._command_lock.locked():
                self._command_lock.release()

        self._emit(
            terminal_kind,
            terminal_message,
            command_id=command_id,
            **terminal_data,
        )
        if raised is not None:
            raise raised
        assert result is not None
        return result

    def capture_voice_timing(self, utterance_id: str, elapsed_ms: float, **metadata: Any) -> None:
        """Content-free STT timing hook; transcript/audio must never be passed here."""
        self._capture("voice_timing", utterance_id, elapsed_ms, **metadata)

    def cancel(self) -> bool:
        event = self._cancel_event
        command_id = self._active_command_id
        if event is None or event.is_set() or command_id is None:
            return False
        self._cancelling = True
        event.set()
        self._capture("cancel_requested")
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
        if self.one_shot_voice_active:
            raise PorterBusyError(
                "Finish or cancel the current voice recording first."
            )
        self._voice_config = config or HandsFreeVoiceConfig()
        service = HandsFreeVoiceService(
            self,
            self._openrouter,
            self._voice_config,
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

    async def listen_once(
        self,
        config: HandsFreeVoiceConfig | None = None,
    ) -> str | None:
        if not self._started:
            raise RuntimeError("PorterRuntime must be started before voice")
        if self._openrouter is None:
            self._emit(
                "voice_unavailable",
                "Voice input requires an OpenRouter API key for transcription.",
                mode="one_shot",
            )
            return None
        if self.busy or self.cancelling:
            raise PorterBusyError("Porter is already working.")
        if self.hands_free_enabled:
            raise PorterBusyError(
                "Disable hands-free listening before using Speak once."
            )
        if self.one_shot_voice_active:
            raise PorterBusyError("A voice recording is already active.")

        selected = config or self._voice_config
        stop_event = threading.Event()
        self._one_shot_stop_event = stop_event
        self._voice_config = selected

        async def capture_and_submit() -> str | None:
            self._emit(
                "voice_once_started",
                "Listening for one command.",
                mode="one_shot",
            )
            try:
                engine = VoiceCaptureEngine(
                    self,
                    self._openrouter,
                    selected,
                )
                captured = await engine.capture_once(
                    stop_event=stop_event,
                    mode="one_shot",
                )
                if stop_event.is_set():
                    self._emit(
                        "voice_once_cancelled",
                        "Voice recording cancelled.",
                        mode="one_shot",
                    )
                    return None
                if captured is None:
                    status = getattr(engine, "last_status", "no_speech")
                    if status in {"error", "transcription_failed"}:
                        self._emit(
                            "voice_once_finished",
                            "Voice input failed.",
                            mode="one_shot",
                            status="failed",
                        )
                    else:
                        self._emit(
                            "voice_once_finished",
                            "No speech detected.",
                            mode="one_shot",
                            status="no_speech",
                        )
                    return None

                transcript = captured.transcript
                self._emit(
                    "voice_once_finished",
                    transcript,
                    mode="one_shot",
                    status="transcribed",
                )
                try:
                    await self.submit(
                        transcript,
                        act=True,
                        input_source="voice",
                        utterance_id=captured.utterance_id,
                    )
                except PorterBusyError:
                    self._emit(
                        "voice_ignored_busy",
                        "Porter is already working.",
                        transcript=transcript,
                        mode="one_shot",
                    )
                    return None
                return transcript
            finally:
                self._one_shot_stop_event = None

        task = asyncio.create_task(
            capture_and_submit(),
            name="porter-one-shot-voice",
        )
        self._one_shot_voice_task = task
        try:
            return await task
        finally:
            if self._one_shot_voice_task is task:
                self._one_shot_voice_task = None

    async def cancel_listen_once(self) -> bool:
        stop_event = self._one_shot_stop_event
        task = self._one_shot_voice_task
        if stop_event is None and (task is None or task.done()):
            return False
        if stop_event is not None:
            stop_event.set()
        if task is not None and task is not asyncio.current_task():
            try:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        return True

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
            "one_shot_voice": self.one_shot_voice_active,
            "cancelling": self.cancelling,
            "visual_click_mode": self.config.visual_click_mode,
            "policy_enabled": self.config.enforce_policy,
            "allow_foreground": self.config.allow_foreground,
            "telemetry": self.telemetry_status,
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
