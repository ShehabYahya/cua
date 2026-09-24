from __future__ import annotations

import asyncio
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from porter_voice import (
    HandsFreeVoiceConfig,
    HandsFreeVoiceService,
    VoiceCaptureEngine,
)
from voice import LocalSpeaker


class FakeMicrophone:
    def __init__(self, **kwargs) -> None:
        self.calls = 0

    def record(
        self,
        *,
        stop_event=None,
        pause_event=None,
        on_speech_start=None,
        on_level=None,
        pre_roll_seconds=0.3,
    ):
        self.calls += 1
        if self.calls == 1:
            if on_level is not None:
                on_level(0.04, 0.02)
            if on_speech_start is not None:
                on_speech_start()
            return b"fake-wav"

        while stop_event is not None and not stop_event.is_set():
            time.sleep(0.01)
        return None


class FakeClient:
    def __init__(self, transcript: str) -> None:
        self.transcript = transcript
        self.calls = 0

    def transcribe_wav(self, wav, *, model, language):
        self.calls += 1
        return self.transcript


class FailingClient:
    def transcribe_wav(self, wav, *, model, language):
        raise TimeoutError("temporary timeout")


class FakeRuntime:
    def __init__(self, *, busy: bool = False) -> None:
        self._busy = busy
        self.events = []
        self.submitted = []
        self.cancelled = 0

    @property
    def busy(self) -> bool:
        return self._busy

    def emit_event(
        self,
        kind,
        message="",
        command_id=None,
        data=None,
        **extra,
    ):
        payload = dict(data or {})
        payload.update(extra)
        self.events.append((kind, message, payload))

    async def submit(self, text, **kwargs):
        self._busy = True
        self.submitted.append((text, kwargs))
        await asyncio.sleep(0.04)
        self._busy = False

    def cancel(self):
        self.cancelled += 1
        return True


class PorterVoiceTest(unittest.TestCase):

    def test_local_speaker_interrupt_is_idempotent(self):
        class FakeProcess:
            def __init__(self):
                self.terminated = 0
                self.waited = 0

            def poll(self):
                return None

            def terminate(self):
                self.terminated += 1

            def kill(self):
                raise AssertionError("kill should not be needed")

            def wait(self, timeout=None):
                self.waited += 1
                return 0

        speaker = LocalSpeaker(enabled=True)
        process = FakeProcess()
        speaker._process = process

        speaker._interrupt_sync()
        speaker._interrupt_sync()

        self.assertEqual(process.terminated, 1)
        self.assertGreaterEqual(process.waited, 1)
        self.assertIsNone(speaker._process)

    def test_speech_is_detected_transcribed_and_submitted_without_button(self):
        runtime = FakeRuntime()
        client = FakeClient("Open Firefox")

        async def scenario():
            service = HandsFreeVoiceService(
                runtime,
                client,
                HandsFreeVoiceConfig(enabled=True, silence_seconds=0.55),
                microphone_factory=FakeMicrophone,
            )
            await service.start()

            for _ in range(100):
                if runtime.submitted:
                    break
                await asyncio.sleep(0.01)

            self.assertEqual(runtime.submitted[0][0], "Open Firefox")
            await service.stop()

        asyncio.run(scenario())

        kinds = [kind for kind, _, _ in runtime.events]
        self.assertIn("voice_listening_started", kinds)
        self.assertIn("voice_speech_started", kinds)
        self.assertIn("voice_endpoint_detected", kinds)
        self.assertIn("voice_transcribing", kinds)
        self.assertIn("voice_transcript", kinds)
        self.assertIn("voice_command_accepted", kinds)
        self.assertIn("voice_listening_stopped", kinds)
        self.assertEqual(client.calls, 1)

    def test_stop_phrase_cancels_active_command_instead_of_queueing(self):
        runtime = FakeRuntime(busy=True)
        client = FakeClient("stop")

        async def scenario():
            service = HandsFreeVoiceService(
                runtime,
                client,
                microphone_factory=FakeMicrophone,
            )
            await service.start()

            for _ in range(100):
                if runtime.cancelled:
                    break
                await asyncio.sleep(0.01)

            self.assertEqual(runtime.cancelled, 1)
            self.assertEqual(runtime.submitted, [])
            await service.stop()

        asyncio.run(scenario())

        kinds = [kind for kind, _, _ in runtime.events]
        self.assertIn("voice_cancel_requested", kinds)

    def test_transcription_failure_is_recoverable_and_keeps_listening(self):
        runtime = FakeRuntime()

        async def scenario():
            service = HandsFreeVoiceService(
                runtime,
                FailingClient(),
                microphone_factory=FakeMicrophone,
            )
            await service.start()
            for _ in range(100):
                if any(
                    kind == "voice_transcription_failed"
                    for kind, _, _ in runtime.events
                ):
                    break
                await asyncio.sleep(0.01)

            self.assertTrue(service.running)
            await service.stop()

        asyncio.run(scenario())

        kinds = [kind for kind, _, _ in runtime.events]
        self.assertIn("voice_transcription_failed", kinds)
        self.assertNotIn("voice_error", kinds)

    def test_shared_capture_engine_records_one_utterance(self):
        runtime = FakeRuntime()
        client = FakeClient("Open Settings")
        engine = VoiceCaptureEngine(
            runtime,
            client,
            HandsFreeVoiceConfig(enabled=False),
            microphone_factory=FakeMicrophone,
        )

        async def scenario():
            stop = threading.Event()
            result = await engine.capture_once(
                stop_event=stop,
                mode="one_shot",
            )
            self.assertIsNotNone(result)
            self.assertEqual(result.transcript, "Open Settings")

        asyncio.run(scenario())

        self.assertEqual(client.calls, 1)
        levels = [
            event for event in runtime.events if event[0] == "voice_level"
        ]
        self.assertEqual(len(levels), 1)
        self.assertEqual(levels[0][2]["mode"], "one_shot")

    def test_cancelled_one_shot_discards_capture_before_stt(self):
        runtime = FakeRuntime()
        client = FakeClient("should not submit")
        engine = VoiceCaptureEngine(
            runtime,
            client,
            HandsFreeVoiceConfig(enabled=False),
            microphone_factory=FakeMicrophone,
        )

        async def scenario():
            stop = threading.Event()
            stop.set()
            result = await engine.capture_once(
                stop_event=stop,
                mode="one_shot",
            )
            self.assertIsNone(result)

        asyncio.run(scenario())
        self.assertEqual(client.calls, 0)


if __name__ == "__main__":
    unittest.main()
