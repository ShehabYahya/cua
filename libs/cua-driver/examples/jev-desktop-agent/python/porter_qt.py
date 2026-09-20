from __future__ import annotations

import asyncio
import concurrent.futures
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
from runtime import PorterRuntime, PorterRuntimeConfig


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
    stopped = Signal()

    def __init__(
        self,
        config: PorterRuntimeConfig,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runtime: PorterRuntime | None = None
        self._shutdown_requested = False

    def run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        runtime = PorterRuntime(
            self._config,
            event_sink=self._forward_event,
        )
        self._runtime = runtime

        async def bootstrap() -> None:
            try:
                await runtime.start()
            except Exception as error:
                self.runtimeFailed.emit(str(error))
                loop.call_soon(loop.stop)
                return
            self.runtimeReady.emit()

        loop.create_task(bootstrap())
        try:
            loop.run_forever()
        finally:
            try:
                if runtime.started:
                    loop.run_until_complete(runtime.stop())
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

    def _forward_event(self, event: RuntimeEvent) -> None:
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
        runtime = self._runtime
        if not text or runtime is None:
            return

        future = self._schedule(
            runtime.submit(
                text,
                act=True,
                allow_foreground=True,
            )
        )
        if future is None:
            self.commandFailed.emit("Porter runtime is not ready.")
            return

        def finished(done: concurrent.futures.Future) -> None:
            try:
                result = done.result()
            except Exception as error:
                self.commandFailed.emit(str(error))
                return
            self.commandFinished.emit(result.status, result.message)

        future.add_done_callback(finished)

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
    listeningChanged = Signal()
    transcriptChanged = Signal()
    lastCommandChanged = Signal()

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
        self._listening = False
        self._transcript = ""
        self._last_command = ""

        worker.eventReceived.connect(self._on_runtime_event)
        worker.runtimeReady.connect(self._on_runtime_ready)
        worker.runtimeFailed.connect(self._on_runtime_failed)
        worker.commandFinished.connect(self._on_command_finished)
        worker.commandFailed.connect(self._on_command_failed)

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

    @Property(bool, notify=listeningChanged)
    def listening(self) -> bool:
        return self._listening

    @Property(str, notify=transcriptChanged)
    def transcript(self) -> str:
        return self._transcript

    @Property(str, notify=lastCommandChanged)
    def lastCommand(self) -> str:
        return self._last_command

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

    def _set_listening(self, value: bool) -> None:
        value = bool(value)
        if value == self._listening:
            return
        self._listening = value
        self.listeningChanged.emit()

    def _set_transcript(self, value: str) -> None:
        if value == self._transcript:
            return
        self._transcript = value
        self.transcriptChanged.emit()

    @Slot(str)
    def submitCommand(self, command: str) -> None:
        text = command.strip()
        if not text or self._busy:
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
        if not self._busy:
            return
        self._set_status("Cancelling…")
        self._worker.cancel()

    @Slot()
    def toggleListening(self) -> None:
        # Chunk 2 exposes the control/state but does not start the hands-free
        # microphone service yet. Chunk 3 wires this to the persistent voice
        # controller without changing QML.
        self._set_listening(not self._listening)
        if self._listening:
            self._set_status("Voice setup pending")
            self._set_detail("Hands-free listening is wired in the next chunk")
        else:
            self._set_status("Ready")
            self._set_detail("Type a command or open the main window")

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
            self._set_status("Cancelling…")
        elif kind == "command_completed":
            self._set_busy(False)
            status = str(event.data.get("status") or "completed")
            if status == "completed":
                self._set_state("ready")
                self._set_status("Done")
            elif status == "cancelled":
                self._set_state("ready")
                self._set_status("Cancelled")
            else:
                self._set_state("attention")
                self._set_status(status.replace("_", " ").title())
            self._set_detail(event.message)
        elif kind in {"command_failed", "runtime_failed"}:
            self._set_busy(False)
            self._set_state("error")
            self._set_status("Porter needs attention")
            self._set_detail(event.message)

    @Slot()
    def _on_runtime_ready(self) -> None:
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
        self._set_detail(message)

    @Slot(str)
    def _on_command_failed(self, message: str) -> None:
        self._set_busy(False)
        self._set_state("error")
        self._set_status("Command failed")
        self._set_detail(message)
