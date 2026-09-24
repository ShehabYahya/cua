from __future__ import annotations

import asyncio
import concurrent.futures
import json
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import (
    QObject,
    Property,
    QThread,
    Signal,
    Slot,
)

from events import RuntimeEvent
from global_shortcuts import GlobalShortcutConfig, GlobalShortcutPortal
from porter_voice import HandsFreeVoiceConfig
from runtime import PorterBusyError, PorterRuntime, PorterRuntimeConfig


@dataclass(frozen=True)
class CommandResultView:
    status: str
    message: str


class PorterRuntimeThread(QThread):
    """Own one asyncio loop and one long-lived PorterRuntime.

    Qt remains entirely on the main thread. Cua/MCP/Jev/OpenRouter stay on this
    worker thread's asyncio loop for the lifetime of the application.
    """

    eventReceived = Signal(object)
    runtimeReady = Signal()
    runtimeFailed = Signal(str)
    commandFinished = Signal(str, str)
    commandFailed = Signal(str)
    commandRejected = Signal(str)
    reconfigured = Signal()
    reconfigureFailed = Signal(str)
    shortcutActivated = Signal()
    shortcutStatusChanged = Signal(str)
    diagnosticsReady = Signal(object)
    diagnosticsFailed = Signal(str)
    stopped = Signal()

    def __init__(
        self,
        config: PorterRuntimeConfig,
        voice_config: HandsFreeVoiceConfig | None = None,
        shortcut_config: GlobalShortcutConfig | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._voice_config = voice_config or HandsFreeVoiceConfig()
        self._shortcut_config = shortcut_config or GlobalShortcutConfig()
        self._shortcut_service: GlobalShortcutPortal | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runtime: PorterRuntime | None = None
        self._shutdown_requested = False
        self._terminal_event_serial = 0

    def run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        runtime = PorterRuntime(
            self._config,
            event_sink=self._forward_event,
        )
        self._runtime = runtime

        shortcut_service = GlobalShortcutPortal(
            self._shortcut_config,
            on_activated=lambda: self.shortcutActivated.emit(),
            on_status=lambda message: self.shortcutStatusChanged.emit(message),
        )
        self._shortcut_service = shortcut_service

        async def bootstrap() -> None:
            if self._shortcut_config.enabled:
                try:
                    await shortcut_service.start()
                except Exception as error:
                    self.shortcutStatusChanged.emit(
                        f"Global shortcut unavailable: {error}"
                    )
            else:
                self.shortcutStatusChanged.emit("Global shortcut disabled")

            try:
                await runtime.start()
                if self._voice_config.enabled:
                    await runtime.start_hands_free(self._voice_config)
            except Exception as error:
                # Keep the Qt/backend worker alive so the native settings UI can
                # accept credentials or corrected provider settings and then
                # reconfigure the runtime without restarting Porter.
                self.runtimeFailed.emit(str(error))
                return
            self.runtimeReady.emit()

        loop.create_task(bootstrap())
        try:
            loop.run_forever()
        finally:
            try:
                service = self._shortcut_service
                if service is not None:
                    loop.run_until_complete(service.stop())
            except Exception:
                pass
            try:
                current = self._runtime
                if current is not None and current.started:
                    loop.run_until_complete(current.stop())
            except Exception:
                pass
            pending = [
                task for task in asyncio.all_tasks(loop) if not task.done()
            ]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()
            self._runtime = None
            self._loop = None
            self.stopped.emit()

    @property
    def voice_config(self) -> HandsFreeVoiceConfig:
        return self._voice_config

    @property
    def shortcut_config(self) -> GlobalShortcutConfig:
        return self._shortcut_config

    def _forward_event(self, event: RuntimeEvent) -> None:
        if event.kind in {"command_completed", "command_failed"}:
            self._terminal_event_serial += 1
        self.eventReceived.emit(event)

    def _schedule(self, coroutine) -> concurrent.futures.Future | None:
        loop = self._loop
        if loop is None or not loop.is_running():
            try:
                coroutine.close()
            except Exception:
                pass
            return None
        return asyncio.run_coroutine_threadsafe(coroutine, loop)

    @Slot(str)
    def submit(self, command: str) -> None:
        text = command.strip()
        if not text:
            return
        runtime = self._runtime
        if runtime is None:
            self.commandFailed.emit("Porter runtime is not ready.")
            return

        future = self._schedule(
            runtime.submit(
                text,
                act=True,
            )
        )
        if future is None:
            self.commandFailed.emit("Porter runtime is not ready.")
            return
        terminal_event_serial = self._terminal_event_serial

        def finished(done: concurrent.futures.Future) -> None:
            try:
                result = done.result()
            except PorterBusyError as error:
                self.commandRejected.emit(str(error))
                return
            except Exception as error:
                if self._terminal_event_serial == terminal_event_serial:
                    self.commandFailed.emit(str(error))
                return
            if self._terminal_event_serial == terminal_event_serial:
                self.commandFinished.emit(result.status, result.message)

        future.add_done_callback(finished)

    @Slot(bool)
    def setListening(self, enabled: bool) -> None:
        runtime = self._runtime
        if runtime is None:
            self.commandFailed.emit("Porter runtime is not ready.")
            return
        future = self._schedule(
            runtime.set_hands_free(
                bool(enabled),
                self._voice_config,
            )
        )
        if future is None:
            self.commandFailed.emit("Porter runtime is not ready.")
            return

        def finished(done: concurrent.futures.Future) -> None:
            try:
                done.result()
            except Exception as error:
                self.commandFailed.emit(str(error))

        future.add_done_callback(finished)

    @Slot()
    def startOneShotListening(self) -> None:
        runtime = self._runtime
        if runtime is None:
            self.commandFailed.emit("Porter runtime is not ready.")
            return
        future = self._schedule(runtime.listen_once(self._voice_config))
        if future is None:
            self.commandFailed.emit("Porter runtime is not ready.")
            return

        def finished(done: concurrent.futures.Future) -> None:
            try:
                done.result()
            except PorterBusyError as error:
                self.commandRejected.emit(str(error))
            except Exception as error:
                self.commandFailed.emit(str(error))

        future.add_done_callback(finished)

    @Slot()
    def cancelOneShotListening(self) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        future = self._schedule(runtime.cancel_listen_once())
        if future is None:
            return
        future.add_done_callback(lambda done: done.exception())

    def reconfigure(
        self,
        config: PorterRuntimeConfig,
        voice_config: HandsFreeVoiceConfig,
        shortcut_config: GlobalShortcutConfig | None = None,
    ) -> concurrent.futures.Future | None:
        loop = self._loop
        if loop is None or not loop.is_running():
            return None

        async def apply() -> None:
            current = self._runtime
            if current is None:
                raise RuntimeError("Porter runtime is not ready.")
            if current.busy or current.cancelling:
                raise RuntimeError(
                    "Wait for the current Porter command to finish before applying settings."
                )
            if current.one_shot_voice_active:
                raise RuntimeError(
                    "Finish or cancel the current voice recording before applying settings."
                )

            old_config = self._config
            old_voice_config = self._voice_config
            old_shortcut_config = self._shortcut_config
            was_listening = current.hands_free_enabled

            await current.stop()

            replacement_runtime = PorterRuntime(
                config,
                event_sink=self._forward_event,
            )
            next_shortcut = shortcut_config or old_shortcut_config

            try:
                await replacement_runtime.start()
                if voice_config.enabled:
                    await replacement_runtime.start_hands_free(
                        voice_config
                    )

                if next_shortcut != old_shortcut_config:
                    old_service = self._shortcut_service
                    if old_service is not None:
                        await old_service.stop()

                    new_service = GlobalShortcutPortal(
                        next_shortcut,
                        on_activated=lambda: self.shortcutActivated.emit(),
                        on_status=lambda message: self.shortcutStatusChanged.emit(
                            message
                        ),
                    )
                    self._shortcut_service = new_service
                    if next_shortcut.enabled:
                        try:
                            await new_service.start()
                        except Exception as error:
                            self.shortcutStatusChanged.emit(
                                f"Global shortcut unavailable: {error}"
                            )
                    else:
                        self.shortcutStatusChanged.emit(
                            "Global shortcut disabled"
                        )
            except Exception as error:
                try:
                    await replacement_runtime.stop()
                except Exception:
                    pass

                # Keep settings failures non-destructive. If the proposed
                # runtime cannot start, restore the previous backend instead of
                # leaving Porter dead until a manual restart.
                rollback_error = None
                try:
                    await current.start()
                    if old_voice_config.enabled and was_listening:
                        await current.start_hands_free(
                            old_voice_config
                        )
                except Exception as rollback:
                    rollback_error = rollback

                self._runtime = current
                self._config = old_config
                self._voice_config = old_voice_config
                self._shortcut_config = old_shortcut_config

                if rollback_error is not None:
                    raise RuntimeError(
                        f"{error}; previous backend also failed to restart: "
                        f"{rollback_error}"
                    ) from error
                raise

            self._runtime = replacement_runtime
            self._config = config
            self._voice_config = voice_config
            self._shortcut_config = next_shortcut
            self.reconfigured.emit()

        future = asyncio.run_coroutine_threadsafe(apply(), loop)

        def finished(done: concurrent.futures.Future) -> None:
            try:
                done.result()
            except Exception as error:
                self.reconfigureFailed.emit(str(error))

        future.add_done_callback(finished)
        return future

    @Slot()
    def requestDiagnostics(self) -> None:
        runtime = self._runtime
        loop = self._loop
        if loop is None or not loop.is_running():
            self.diagnosticsFailed.emit("Porter worker is not running.")
            return

        async def collect():
            if runtime is not None and runtime.started:
                return await runtime.diagnostics()
            return await PorterRuntime.preflight()

        future = asyncio.run_coroutine_threadsafe(collect(), loop)

        def finished(done: concurrent.futures.Future) -> None:
            try:
                payload = done.result()
            except Exception as error:
                self.diagnosticsFailed.emit(str(error))
                return
            self.diagnosticsReady.emit(payload)

        future.add_done_callback(finished)

    @Slot()
    def restartBackend(self) -> None:
        future = self.reconfigure(
            self._config,
            self._voice_config,
            self._shortcut_config,
        )
        if future is None:
            self.reconfigureFailed.emit(
                "Porter worker is not running."
            )

    @Slot()
    def cancel(self) -> None:
        runtime = self._runtime
        loop = self._loop
        if runtime is None or loop is None or not loop.is_running():
            return
        loop.call_soon_threadsafe(runtime.cancel)

    @Slot()
    def requestShutdown(self) -> None:
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        runtime = self._runtime
        loop = self._loop
        if loop is None or not loop.is_running():
            return

        async def stop_runtime() -> None:
            try:
                service = self._shortcut_service
                if service is not None:
                    await service.stop()
                if runtime is not None:
                    await runtime.stop()
            finally:
                loop.call_soon(loop.stop)

        asyncio.run_coroutine_threadsafe(stop_runtime(), loop)


class PorterViewModel(QObject):
    """QML-facing state model.

    The QML layer contains presentation only. It never imports Cua/Jev or parses
    progress strings for control flow; structured runtime events update these
    properties on the Qt main thread.
    """

    stateChanged = Signal()
    statusTextChanged = Signal()
    detailTextChanged = Signal()
    busyChanged = Signal()
    cancellingChanged = Signal()
    listeningChanged = Signal()
    manualVoiceActiveChanged = Signal()
    voiceInputStateChanged = Signal()
    micLevelChanged = Signal()
    voiceSilenceSecondsChanged = Signal()
    transcriptChanged = Signal()
    lastCommandChanged = Signal()
    shortcutStatusChanged = Signal()
    diagnosticsChanged = Signal()

    toggleCompactRequested = Signal()
    showMainRequested = Signal()
    quitRequested = Signal()

    def __init__(
        self,
        worker: PorterRuntimeThread,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._worker = worker
        self._state = "starting"
        self._status_text = "Starting Porter…"
        self._detail_text = "Connecting to the desktop runtime"
        self._busy = False
        self._cancelling = False
        self._listening = False
        self._manual_voice_active = False
        self._voice_input_state = "idle"
        self._mic_level = 0.0
        self._transcript = ""
        self._last_command = ""
        self._shortcut_status = "Global shortcut starting…"
        self._diagnostics_status = "Not checked"
        self._diagnostics_summary = "Run diagnostics to inspect Cua and Porter."
        self._diagnostics_details = ""
        self._diagnostics_running = False
        self._voice_silence_seconds = worker.voice_config.silence_seconds

        worker.eventReceived.connect(self._on_runtime_event)
        worker.runtimeReady.connect(self._on_runtime_ready)
        worker.runtimeFailed.connect(self._on_runtime_failed)
        worker.commandFinished.connect(self._on_command_finished)
        worker.commandFailed.connect(self._on_command_failed)
        worker.commandRejected.connect(self._on_command_rejected)
        worker.reconfigured.connect(self._on_runtime_reconfigured)
        worker.reconfigureFailed.connect(self._on_runtime_reconfigure_failed)
        worker.shortcutStatusChanged.connect(self._on_shortcut_status)
        worker.diagnosticsReady.connect(self._on_diagnostics_ready)
        worker.diagnosticsFailed.connect(self._on_diagnostics_failed)

    @Property(str, notify=stateChanged)
    def state(self) -> str:
        return self._state

    @Property(str, notify=statusTextChanged)
    def statusText(self) -> str:
        return self._status_text

    @Property(str, notify=detailTextChanged)
    def detailText(self) -> str:
        return self._detail_text

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @Property(bool, notify=cancellingChanged)
    def cancelling(self) -> bool:
        return self._cancelling

    @Property(bool, notify=listeningChanged)
    def listening(self) -> bool:
        return self._listening

    @Property(bool, notify=listeningChanged)
    def handsFreeActive(self) -> bool:
        return self._listening

    @Property(bool, notify=manualVoiceActiveChanged)
    def manualVoiceActive(self) -> bool:
        return self._manual_voice_active

    @Property(str, notify=voiceInputStateChanged)
    def voiceInputState(self) -> str:
        return self._voice_input_state

    @Property(float, notify=micLevelChanged)
    def micLevel(self) -> float:
        return self._mic_level

    @Property(str, notify=transcriptChanged)
    def transcript(self) -> str:
        return self._transcript

    @Property(float, notify=voiceSilenceSecondsChanged)
    def voiceSilenceSeconds(self) -> float:
        return self._voice_silence_seconds

    @Property(str, notify=lastCommandChanged)
    def lastCommand(self) -> str:
        return self._last_command

    @Property(str, notify=shortcutStatusChanged)
    def shortcutStatus(self) -> str:
        return self._shortcut_status

    @Property(str, notify=diagnosticsChanged)
    def diagnosticsStatus(self) -> str:
        return self._diagnostics_status

    @Property(str, notify=diagnosticsChanged)
    def diagnosticsSummary(self) -> str:
        return self._diagnostics_summary

    @Property(str, notify=diagnosticsChanged)
    def diagnosticsDetails(self) -> str:
        return self._diagnostics_details

    @Property(bool, notify=diagnosticsChanged)
    def diagnosticsRunning(self) -> bool:
        return self._diagnostics_running

    def _set_state(self, value: str) -> None:
        if value == self._state:
            return
        self._state = value
        self.stateChanged.emit()

    def _set_status(self, value: str) -> None:
        if value == self._status_text:
            return
        self._status_text = value
        self.statusTextChanged.emit()

    def _set_detail(self, value: str) -> None:
        if value == self._detail_text:
            return
        self._detail_text = value
        self.detailTextChanged.emit()

    def _set_busy(self, value: bool) -> None:
        value = bool(value)
        if value == self._busy:
            return
        self._busy = value
        self.busyChanged.emit()

    def _set_cancelling(self, value: bool) -> None:
        value = bool(value)
        if value == self._cancelling:
            return
        self._cancelling = value
        self.cancellingChanged.emit()

    def _set_listening(self, value: bool) -> None:
        value = bool(value)
        if value == self._listening:
            return
        self._listening = value
        self.listeningChanged.emit()

    def _set_manual_voice_active(self, value: bool) -> None:
        value = bool(value)
        if value == self._manual_voice_active:
            return
        self._manual_voice_active = value
        self.manualVoiceActiveChanged.emit()

    def _set_voice_input_state(self, value: str) -> None:
        value = str(value)
        if value == self._voice_input_state:
            return
        self._voice_input_state = value
        self.voiceInputStateChanged.emit()

    def _set_mic_level(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._mic_level) < 0.01:
            return
        self._mic_level = value
        self.micLevelChanged.emit()

    def _set_transcript(self, value: str) -> None:
        if value == self._transcript:
            return
        self._transcript = value
        self.transcriptChanged.emit()

    @Slot(str)
    def submitCommand(self, command: str) -> None:
        text = command.strip()
        if (
            not text
            or self._busy
            or self._cancelling
            or self._manual_voice_active
        ):
            return
        self._last_command = text
        self.lastCommandChanged.emit()
        self._set_busy(True)
        self._set_state("working")
        self._set_status("Working…")
        self._set_detail(text)
        self._worker.submit(text)

    @Slot()
    def cancelCurrent(self) -> None:
        if not self._busy or self._cancelling:
            return
        self._set_cancelling(True)
        self._set_status("Stopping…")
        self._worker.cancel()

    @Slot()
    def toggleListening(self) -> None:
        if self._manual_voice_active:
            return
        self._worker.setListening(not self._listening)

    @Slot()
    def startOneShotListening(self) -> None:
        if (
            self._busy
            or self._cancelling
            or self._listening
            or self._manual_voice_active
        ):
            return
        self._worker.startOneShotListening()

    @Slot()
    def cancelOneShotListening(self) -> None:
        if not self._manual_voice_active:
            return
        self._worker.cancelOneShotListening()

    @Slot()
    def toggleOneShotListening(self) -> None:
        if self._manual_voice_active:
            self.cancelOneShotListening()
        else:
            self.startOneShotListening()

    @Slot(bool)
    def setListening(self, enabled: bool) -> None:
        self._worker.setListening(bool(enabled))

    @Slot()
    def refreshDiagnostics(self) -> None:
        if self._diagnostics_running:
            return
        self._diagnostics_running = True
        self._diagnostics_status = "Checking…"
        self._diagnostics_summary = "Reading Cua Driver health and capabilities."
        self._diagnostics_details = ""
        self.diagnosticsChanged.emit()
        self._worker.requestDiagnostics()

    @Slot()
    def restartBackend(self) -> None:
        if self._busy or self._cancelling:
            self._set_status("Finish the current task first")
            return
        if self._manual_voice_active:
            self._set_status("Finish or cancel the current voice recording first")
            return
        self._set_state("starting")
        self._set_status("Restarting Porter backend…")
        self._set_detail(
            "Reconnecting Cua Driver, Jev, and voice services"
        )
        self._worker.restartBackend()

    @Slot()
    def toggleCompact(self) -> None:
        self.toggleCompactRequested.emit()

    @Slot()
    def showMain(self) -> None:
        self.showMainRequested.emit()

    @Slot()
    def requestQuit(self) -> None:
        self.quitRequested.emit()

    @Slot(object)
    def _on_runtime_event(self, event: Any) -> None:
        if not isinstance(event, RuntimeEvent):
            return
        kind = event.kind
        if kind == "runtime_starting":
            self._set_state("starting")
            self._set_status("Starting Porter…")
            self._set_detail(event.message)
        elif kind == "runtime_ready":
            self._set_state("ready")
            self._set_status("Ready")
            self._set_detail("Porter is connected to your desktop")
        elif kind == "command_started":
            self._set_busy(True)
            self._set_state("working")
            self._set_status("Working…")
            self._set_detail(event.message)
        elif kind == "agent_progress":
            self._set_status(event.message or "Working…")
        elif kind == "command_cancel_requested":
            self._set_cancelling(True)
            self._set_status("Stopping…")
        elif kind == "voice_listening_started":
            self._set_listening(True)
            self._set_mic_level(0.0)
            silence = event.data.get("silence_seconds")
            if silence is not None:
                try:
                    value = float(silence)
                except (TypeError, ValueError):
                    value = self._voice_silence_seconds
                if abs(value - self._voice_silence_seconds) >= 0.001:
                    self._voice_silence_seconds = value
                    self.voiceSilenceSecondsChanged.emit()
            if not self._busy:
                self._set_state("ready")
                self._set_status("Listening")
                self._set_detail("Speak naturally — Porter will submit when you stop")
        elif kind == "voice_listening_stopped":
            self._set_listening(False)
            self._set_mic_level(0.0)
            if not self._busy:
                self._set_state("ready")
                self._set_status("Ready")
                self._set_detail("Type a command or enable hands-free listening")
        elif kind == "voice_once_started":
            self._set_manual_voice_active(True)
            self._set_voice_input_state("waiting")
            self._set_mic_level(0.0)
            self._set_state("listening")
            self._set_status("Listening…")
            self._set_detail("Speak one command")
        elif kind == "voice_once_cancelled":
            self._set_manual_voice_active(False)
            self._set_voice_input_state("idle")
            self._set_mic_level(0.0)
            if not self._busy:
                self._set_state("ready")
                self._set_status("Ready")
                self._set_detail("Voice recording cancelled")
        elif kind == "voice_once_finished":
            self._set_manual_voice_active(False)
            self._set_voice_input_state("idle")
            self._set_mic_level(0.0)
            if event.data.get("status") == "no_speech" and not self._busy:
                self._set_state("ready")
                self._set_status("Ready")
                self._set_detail("No speech detected")
        elif kind == "voice_speech_started":
            if event.data.get("mode") == "one_shot":
                self._set_voice_input_state("speech")
            self._set_state("listening")
            self._set_status("Listening…")
            self._set_detail("Keep speaking")
        elif kind == "voice_level":
            self._set_mic_level(float(event.data.get("level") or 0.0))
        elif kind == "voice_endpoint_detected":
            self._set_mic_level(0.0)
            self._set_status("Finishing…")
        elif kind == "voice_transcribing":
            if event.data.get("mode") == "one_shot":
                self._set_voice_input_state("transcribing")
            self._set_state("working")
            self._set_status("Transcribing…")
        elif kind == "voice_transcript":
            transcript = str(event.data.get("transcript") or event.message or "")
            self._set_transcript(transcript)
            self._set_detail(transcript)
        elif kind == "voice_command_accepted":
            transcript = str(event.data.get("transcript") or event.message or "")
            self._set_transcript(transcript)
            self._set_busy(True)
            self._set_state("working")
            self._set_status("Working…")
            self._set_detail(transcript)
        elif kind == "voice_cancel_requested":
            self._set_status("Cancelling…")
        elif kind == "voice_ignored_busy":
            self._set_status("Already working")
            self._set_detail(event.message)
        elif kind == "voice_unavailable":
            self._set_listening(False)
            self._set_state("attention")
            self._set_status("Voice unavailable")
            self._set_detail(event.message)
        elif kind == "voice_transcription_failed":
            self._set_mic_level(0.0)
            if event.data.get("mode") == "one_shot":
                self._set_manual_voice_active(False)
                self._set_voice_input_state("idle")
                self._set_state("attention")
                self._set_status("Transcription failed")
            else:
                self._set_state("attention")
                self._set_status("Transcription failed — still listening")
            self._set_detail(event.message)
        elif kind in {"voice_error", "voice_command_failed"}:
            self._set_listening(False)
            self._set_mic_level(0.0)
            self._set_state("error")
            self._set_status("Voice needs attention")
            self._set_detail(event.message)
        elif kind == "command_completed":
            self._set_busy(False)
            self._set_cancelling(False)
            status = str(event.data.get("status") or "completed")
            if status == "completed":
                self._set_state("ready")
                self._set_status("Done")
            elif status == "cancelled":
                self._set_state("ready")
                self._set_status("Ready")
                self._set_detail("Previous task cancelled.")
            else:
                self._set_state("attention")
                self._set_status(status.replace("_", " ").title())
            self._set_detail(event.message)
            self._set_mic_level(0.0)
        elif kind in {"command_failed", "runtime_failed"}:
            self._set_busy(False)
            self._set_cancelling(False)
            self._set_state("error")
            self._set_status("Porter needs attention")
            self._set_detail(event.message)

    @Slot()
    def _on_runtime_ready(self) -> None:
        self._set_busy(False)
        self._set_cancelling(False)
        self._set_state("ready")
        self._set_status("Ready")
        self._set_detail("Porter is connected to your desktop")

    @Slot(str)
    def _on_runtime_failed(self, message: str) -> None:
        self._set_busy(False)
        self._set_state("error")
        self._set_status("Porter could not start")
        self._set_detail(message)

    @Slot(str, str)
    def _on_command_finished(self, status: str, message: str) -> None:
        # Structured command_completed normally arrives first. This signal is a
        # fallback for UI consistency if a custom runtime event sink is changed.
        self._set_busy(False)
        if status == "completed":
            self._set_state("ready")
            self._set_status("Done")
        elif status == "cancelled":
            self._set_state("ready")
            self._set_status("Cancelled")
        else:
            self._set_state("attention")
            self._set_status(status.replace("_", " ").title())
        self._set_detail(message)

    @Slot(str)
    def _on_command_failed(self, message: str) -> None:
        self._set_busy(False)
        self._set_cancelling(False)
        self._set_state("error")
        self._set_status("Command failed")
        self._set_detail(message)

    @Slot(str)
    def _on_command_rejected(self, message: str) -> None:
        if self._busy or self._cancelling:
            return
        self._set_state("attention")
        self._set_status("Porter is busy")
        self._set_detail(message)

    @Slot()
    def _on_runtime_reconfigured(self) -> None:
        self._set_busy(False)
        self._set_state("ready")
        self._set_status("Ready")
        self._set_detail("Porter backend restarted")

    @Slot(str)
    def _on_runtime_reconfigure_failed(self, message: str) -> None:
        self._set_busy(False)
        self._set_state("error")
        self._set_status("Backend restart failed")
        self._set_detail(message)

    @Slot(str)
    def _on_shortcut_status(self, message: str) -> None:
        if message == self._shortcut_status:
            return
        self._shortcut_status = message
        self.shortcutStatusChanged.emit()

    @Slot(object)
    def _on_diagnostics_ready(self, payload: Any) -> None:
        data = payload if isinstance(payload, dict) else {}
        warnings = list(data.get("warnings") or [])
        limitations = list(data.get("limitations") or [])
        status = str(data.get("status") or "ok")
        windows = int(data.get("visible_windows") or 0)
        apps = int(data.get("known_apps") or 0)
        capabilities = data.get("capabilities") or {}

        self._diagnostics_running = False
        self._diagnostics_status = (
            "Healthy" if status == "ok" else "Needs attention"
        )
        self._diagnostics_summary = (
            f"{windows} visible window(s), {apps} known app(s), "
            f"{len(warnings)} health warning(s), "
            f"{len(limitations)} limitation(s)."
        )

        lines = []
        if warnings:
            lines.append("Warnings:")
            lines.extend(f"• {item}" for item in warnings)
        if limitations:
            if lines:
                lines.append("")
            lines.append("Limitations:")
            lines.extend(f"• {item}" for item in limitations)
        if capabilities:
            if lines:
                lines.append("")
            lines.append("Capabilities:")
            lines.append(
                json.dumps(
                    capabilities,
                    indent=2,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        for key, label in (
            ("provider", "Provider"),
            ("jev_model", "Jev model"),
            ("visual_click_mode", "Visual clicks"),
            ("platform", "Platform"),
            ("python", "Python"),
        ):
            value = data.get(key)
            if value not in {None, ""}:
                if not lines:
                    lines.append("Runtime:")
                lines.append(f"{label}: {value}")

        self._diagnostics_details = "\n".join(lines)
        self.diagnosticsChanged.emit()

    @Slot(str)
    def _on_diagnostics_failed(self, message: str) -> None:
        self._diagnostics_running = False
        self._diagnostics_status = "Unavailable"
        self._diagnostics_summary = message or "Diagnostics could not run."
        self._diagnostics_details = ""
        self.diagnosticsChanged.emit()
