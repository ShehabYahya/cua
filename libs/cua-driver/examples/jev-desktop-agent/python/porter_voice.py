from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from openrouter_client import DEFAULT_STT_MODEL, OpenRouterClient
from voice import Microphone

if TYPE_CHECKING:
    from runtime import PorterRuntime


_CANCEL_PHRASES = {
    "cancel",
    "cancel that",
    "stop",
    "stop now",
    "stop that",
    "never mind",
    "nevermind",
}


@dataclass(frozen=True)
class HandsFreeVoiceConfig:
    enabled: bool = True
    stt_model: str = DEFAULT_STT_MODEL
    language: str | None = None
    microphone_device: int | str | None = None
    silence_seconds: float = 0.55
    max_seconds: float = 20.0
    min_speech_seconds: float = 0.20
    base_threshold: float = 0.012


@dataclass(frozen=True)
class VoiceCaptureResult:
    utterance_id: str
    transcript: str


class VoiceCaptureEngine:
    """Shared single-utterance microphone/VAD/STT pipeline."""

    def __init__(
        self,
        runtime: "PorterRuntime",
        client: OpenRouterClient,
        config: HandsFreeVoiceConfig,
        *,
        microphone_factory: Callable[..., Microphone] = Microphone,
    ) -> None:
        self.runtime = runtime
        self.client = client
        self.config = config
        self.microphone = microphone_factory(
            device=config.microphone_device,
            max_seconds=config.max_seconds,
            silence_seconds=config.silence_seconds,
            min_speech_seconds=config.min_speech_seconds,
            base_threshold=config.base_threshold,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_level_emit = 0.0
        self.last_status = "idle"

    def _thread_emit(
        self,
        mode: str,
        kind: str,
        message: str = "",
        **data,
    ) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        payload = dict(data)
        payload["mode"] = mode
        loop.call_soon_threadsafe(
            self.runtime.emit_event,
            kind,
            message,
            None,
            payload,
        )

    def _capture_stt(
        self,
        utterance_id: str,
        started: float,
        status: str,
        mode: str,
    ) -> None:
        hook = getattr(self.runtime, "capture_voice_timing", None)
        if not callable(hook):
            return
        try:
            hook(
                utterance_id,
                (time.monotonic() - started) * 1000,
                status=status,
                mode=mode,
                model=self.config.stt_model,
                language=self.config.language,
            )
        except Exception:
            pass

    async def capture_once(
        self,
        *,
        stop_event: threading.Event,
        mode: str,
    ) -> VoiceCaptureResult | None:
        self._loop = asyncio.get_running_loop()
        self.last_status = "capturing"

        def on_speech_start() -> None:
            self._thread_emit(mode, "voice_speech_started", "Listening…")

        def on_level(rms: float, threshold: float) -> None:
            level = (
                0.0
                if threshold <= 0
                else min(1.0, max(0.0, rms / (threshold * 1.6)))
            )
            now = time.monotonic()
            if now - self._last_level_emit < 0.05:
                return
            self._last_level_emit = now
            self._thread_emit(mode, "voice_level", "", level=level)

        try:
            wav = await asyncio.to_thread(
                self.microphone.record,
                stop_event=stop_event,
                on_speech_start=on_speech_start,
                on_level=on_level,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.last_status = "error"
            self.runtime.emit_event(
                "voice_error",
                str(error),
                mode=mode,
            )
            return None

        if stop_event.is_set():
            self.last_status = "cancelled"
            return None
        if not wav:
            self.last_status = "no_speech"
            return None

        self.runtime.emit_event(
            "voice_endpoint_detected",
            "Speech ended.",
            mode=mode,
        )
        self.runtime.emit_event(
            "voice_transcribing",
            "Transcribing…",
            mode=mode,
        )
        utterance_id = uuid.uuid4().hex
        stt_started = time.monotonic()
        try:
            text = await asyncio.to_thread(
                self.client.transcribe_wav,
                wav,
                model=self.config.stt_model,
                language=self.config.language,
            )
        except asyncio.CancelledError:
            self.last_status = "cancelled"
            self._capture_stt(
                utterance_id,
                stt_started,
                "cancelled",
                mode,
            )
            raise
        except Exception as error:
            self.last_status = "transcription_failed"
            self._capture_stt(
                utterance_id,
                stt_started,
                "failed",
                mode,
            )
            if not stop_event.is_set():
                self.runtime.emit_event(
                    "voice_transcription_failed",
                    f"Transcription failed: {error}",
                    mode=mode,
                )
            return None

        if stop_event.is_set():
            self.last_status = "cancelled"
            self._capture_stt(
                utterance_id,
                stt_started,
                "cancelled",
                mode,
            )
            return None

        self._capture_stt(
            utterance_id,
            stt_started,
            "transcribed",
            mode,
        )
        transcript = text.strip()
        if not transcript:
            self.last_status = "no_speech"
            return None

        self.last_status = "transcribed"
        self.runtime.emit_event(
            "voice_transcript",
            transcript,
            transcript=transcript,
            mode=mode,
        )
        return VoiceCaptureResult(utterance_id, transcript)


class HandsFreeVoiceService:
    """Resident voice loop built on the shared single-utterance capture engine."""

    def __init__(
        self,
        runtime: "PorterRuntime",
        client: OpenRouterClient,
        config: HandsFreeVoiceConfig | None = None,
        *,
        microphone_factory: Callable[..., Microphone] = Microphone,
    ) -> None:
        self.runtime = runtime
        self.client = client
        self.config = config or HandsFreeVoiceConfig()
        self.capture = VoiceCaptureEngine(
            runtime,
            client,
            self.config,
            microphone_factory=microphone_factory,
        )
        self._stop = threading.Event()
        self._task: asyncio.Task | None = None
        self._active_command: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.runtime.emit_event(
            "voice_listening_started",
            "Hands-free listening is on.",
            silence_seconds=self.config.silence_seconds,
            mode="hands_free",
        )
        self._task = asyncio.create_task(
            self._run(),
            name="porter-hands-free-voice",
        )

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            self._task = None
            self.runtime.emit_event(
                "voice_listening_stopped",
                "Hands-free listening is off.",
                mode="hands_free",
            )

    async def _submit_voice_command(
        self,
        text: str,
        utterance_id: str | None = None,
    ) -> None:
        try:
            await self.runtime.submit(
                text,
                act=True,
                input_source="voice",
                utterance_id=utterance_id,
            )
        except Exception as error:
            if type(error).__name__ == "PorterBusyError":
                self.runtime.emit_event(
                    "voice_ignored_busy",
                    str(error),
                    transcript=text,
                    mode="hands_free",
                )
                return
            self.runtime.emit_event(
                "voice_command_failed",
                str(error),
                transcript=text,
                mode="hands_free",
            )

    async def _run(self) -> None:
        try:
            while not self._stop.is_set():
                result = await self.capture.capture_once(
                    stop_event=self._stop,
                    mode="hands_free",
                )
                if self._stop.is_set():
                    return
                if result is None:
                    if self.capture.last_status == "error":
                        return
                    continue

                transcript = result.transcript
                normalized = transcript.casefold().strip(" .!?\n")
                active = (
                    self._active_command is not None
                    and not self._active_command.done()
                )

                if active or self.runtime.busy or getattr(self.runtime, "cancelling", False):
                    if normalized in _CANCEL_PHRASES:
                        self.runtime.cancel()
                        self.runtime.emit_event(
                            "voice_cancel_requested",
                            "Cancelling…",
                            transcript=transcript,
                            mode="hands_free",
                        )
                    else:
                        self.runtime.emit_event(
                            "voice_ignored_busy",
                            "Porter is already working.",
                            transcript=transcript,
                            mode="hands_free",
                        )
                    continue

                self.runtime.emit_event(
                    "voice_command_accepted",
                    transcript,
                    transcript=transcript,
                    mode="hands_free",
                )
                command_task = asyncio.create_task(
                    self._submit_voice_command(
                        transcript,
                        result.utterance_id,
                    ),
                    name="porter-voice-command",
                )
                self._active_command = command_task

                def clear(done: asyncio.Task) -> None:
                    if self._active_command is done:
                        self._active_command = None

                command_task.add_done_callback(clear)
        finally:
            active = self._active_command
            if active is not None and not active.done():
                self.runtime.cancel()
                await asyncio.gather(active, return_exceptions=True)
