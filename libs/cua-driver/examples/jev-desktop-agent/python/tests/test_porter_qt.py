from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from PySide6.QtCore import QObject, Signal
except ModuleNotFoundError as error:
    raise unittest.SkipTest(
        "PySide6 is an optional native-GUI dependency"
    ) from error

from events import RuntimeEvent
from porter_qt import CommandResultView, PorterRuntimeThread, PorterViewModel
from porter_voice import HandsFreeVoiceConfig
from runtime import PorterRuntimeConfig


class FakeWorker(QObject):
    eventReceived = Signal(object)
    runtimeReady = Signal()
    runtimeFailed = Signal(str)
    commandFinished = Signal(str, str)
    commandFailed = Signal(str)
    commandRejected = Signal(str)
    reconfigured = Signal()
    reconfigureFailed = Signal(str)
    shortcutStatusChanged = Signal(str)
    diagnosticsReady = Signal(object)
    diagnosticsFailed = Signal(str)

    voice_config = HandsFreeVoiceConfig()

    def setListening(self, enabled):
        self.listening_requested = bool(enabled)

    def cancel(self):
        self.cancel_requested = getattr(self, "cancel_requested", 0) + 1

    def startOneShotListening(self):
        self.one_shot_started = getattr(self, "one_shot_started", 0) + 1

    def cancelOneShotListening(self):
        self.one_shot_cancelled = getattr(self, "one_shot_cancelled", 0) + 1


class ControlledFuture:
    def __init__(self, result):
        self._result = result
        self._callback = None

    def add_done_callback(self, callback):
        self._callback = callback

    def result(self):
        return self._result

    def finish(self):
        self._callback(self)


class DummyRuntime:
    async def submit(self, *args, **kwargs):
        raise AssertionError("the controlled scheduler should own this coroutine")


class PorterQtTest(unittest.TestCase):
    def test_stopping_voice_clears_active_listening_state(self):
        model = PorterViewModel(FakeWorker())
        model._on_runtime_event(RuntimeEvent("voice_listening_started"))
        model._on_runtime_event(RuntimeEvent("voice_speech_started"))
        self.assertEqual(model.state, "listening")

        model._on_runtime_event(RuntimeEvent("voice_listening_stopped"))

        self.assertFalse(model.listening)
        self.assertEqual(model.state, "ready")
        self.assertEqual(model.statusText, "Ready")

    def test_recoverable_transcription_failure_keeps_listening_enabled(self):
        model = PorterViewModel(FakeWorker())
        model._on_runtime_event(RuntimeEvent("voice_listening_started"))

        model._on_runtime_event(
            RuntimeEvent(
                "voice_transcription_failed",
                "Transcription failed: temporary timeout",
            )
        )

        self.assertTrue(model.listening)
        self.assertEqual(model.state, "attention")
        self.assertIn("still listening", model.statusText)

    def test_reenabling_voice_clears_previous_error_state(self):
        model = PorterViewModel(FakeWorker())
        model._on_runtime_event(RuntimeEvent("voice_error", "device lost"))
        self.assertEqual(model.state, "error")

        model._on_runtime_event(RuntimeEvent("voice_listening_started"))

        self.assertTrue(model.listening)
        self.assertEqual(model.state, "ready")
        self.assertEqual(model.statusText, "Listening")

    def test_cancel_current_sets_explicit_cancelling_state(self):
        worker = FakeWorker()
        model = PorterViewModel(worker)
        model._on_runtime_event(RuntimeEvent("command_started", "Open Firefox"))

        model.cancelCurrent()

        self.assertTrue(model.cancelling)
        self.assertEqual(model.statusText, "Stopping…")
        self.assertEqual(worker.cancel_requested, 1)

        model._on_runtime_event(
            RuntimeEvent(
                "command_completed",
                "Command cancelled.",
                data={"status": "cancelled"},
            )
        )
        self.assertFalse(model.busy)
        self.assertFalse(model.cancelling)
        self.assertEqual(model.state, "ready")
        self.assertEqual(model.statusText, "Ready")

    def test_one_shot_voice_state_is_separate_from_hands_free(self):
        worker = FakeWorker()
        model = PorterViewModel(worker)

        model._on_runtime_event(
            RuntimeEvent(
                "voice_once_started",
                "Listening for one command.",
                data={"mode": "one_shot"},
            )
        )
        self.assertTrue(model.manualVoiceActive)
        self.assertFalse(model.handsFreeActive)
        self.assertEqual(model.voiceInputState, "waiting")

        model._on_runtime_event(
            RuntimeEvent(
                "voice_speech_started",
                "Listening…",
                data={"mode": "one_shot"},
            )
        )
        self.assertEqual(model.voiceInputState, "speech")

        model._on_runtime_event(
            RuntimeEvent(
                "voice_transcribing",
                "Transcribing…",
                data={"mode": "one_shot"},
            )
        )
        self.assertEqual(model.voiceInputState, "transcribing")

        model._on_runtime_event(
            RuntimeEvent(
                "voice_once_cancelled",
                "Voice recording cancelled.",
                data={"mode": "one_shot"},
            )
        )
        self.assertFalse(model.manualVoiceActive)
        self.assertEqual(model.voiceInputState, "idle")
        self.assertFalse(model.handsFreeActive)

    def test_structured_terminal_event_suppresses_duplicate_fallback(self):
        worker = PorterRuntimeThread(PorterRuntimeConfig())
        worker._runtime = DummyRuntime()
        future = ControlledFuture(CommandResultView("completed", "done"))

        def schedule(coroutine):
            coroutine.close()
            return future

        worker._schedule = schedule
        fallbacks = []
        worker.commandFinished.connect(
            lambda status, message: fallbacks.append((status, message))
        )

        worker.submit("Open Firefox")
        worker._forward_event(RuntimeEvent("command_completed", "done"))
        future.finish()

        self.assertEqual(fallbacks, [])


if __name__ == "__main__":
    unittest.main()
