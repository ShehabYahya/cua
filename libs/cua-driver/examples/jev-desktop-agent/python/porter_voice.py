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


class HandsFreeVoiceService:
    """Resident local-VAD -> STT -> Porter command loop.

    The microphone is continuously sampled locally while enabled. Silence is
    discarded immediately. Only a bounded utterance that local VAD classified
    as speech is sent to STT. The service keeps listening while Porter acts so a
    spoken cancellation phrase can stop the active command.
    """

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
        self.microphone = microphone_factory(
            device=self.config.microphone_device,
            max_seconds=self.config.max_seconds,
            silence_seconds=self.config.silence_seconds,
            min_speech_seconds=self.config.min_speech_seconds,
            base_threshold=self.config.base_threshold,
        )
        self._stop = threading.Event()
        self._task: asyncio.Task | None = None
        self._active_command: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_level_emit = 0.0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._loop = asyncio.get_running_loop()
        self.runtime.emit_event(
            "voice_listening_started",
            "Hands-free listening is on.",
            silence_seconds=self.config.silence_seconds,
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
            self._loop = None
            self.runtime.emit_event(
                "voice_listening_stopped",
                "Hands-free listening is off.",
            )

    def _thread_emit(self, kind: str, message: str = "", **data) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(
            self.runtime.emit_event,
            kind,
            message,
            None,
            data,
        )

    def _on_speech_start(self) -> None:
        self._thread_emit("voice_speech_started", "Listening…")

    def _on_level(self, rms: float, threshold: float) -> None:
        if threshold <= 0:
            level = 0.0
        else:
            level = min(1.0, max(0.0, rms / (threshold * 1.6)))
        now = time.monotonic()
        if now - self._last_level_emit < 0.05:
            return
        self._last_level_emit = now
        self._thread_emit("voice_level", "", level=level)

    def _capture_stt(self, utterance_id: str, started: float, status: str) -> None:
        hook = getattr(self.runtime, "capture_voice_timing", None)
        if callable(hook):
            try:
                hook(utterance_id, (time.monotonic() - started) * 1000,
                     status=status, model=self.config.stt_model, language=self.config.language)
            except Exception:
                # Telemetry is never allowed to interrupt listening or control.
                pass

    async def _submit_voice_command(self, text: str, utterance_id: str | None = None) -> None:
        try:
            await self.runtime.submit(
                text,
                act=True,
                input_source="voice",
                utterance_id=utterance_id,
            )
        except Exception as error:
            self.runtime.emit_event(
                "voice_command_failed",
                str(error),
                transcript=text,
            )

    async def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    wav = await asyncio.to_thread(
                        self.microphone.record,
                        stop_event=self._stop,
                        on_speech_start=self._on_speech_start,
                        on_level=self._on_level,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self.runtime.emit_event(
                        "voice_error",
                        str(error),
                    )
                    return

                if self._stop.is_set():
                    return
                if not wav:
                    continue

                self.runtime.emit_event(
                    "voice_endpoint_detected",
                    "Speech ended.",
                )
                self.runtime.emit_event(
                    "voice_transcribing",
                    "Transcribing…",
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
                    self._capture_stt(utterance_id, stt_started, "cancelled")
                    raise
                except Exception as error:
                    self._capture_stt(utterance_id, stt_started, "failed")
                    self.runtime.emit_event(
                        "voice_transcription_failed",
                        f"Transcription failed: {error}",
                    )
                    continue

                self._capture_stt(utterance_id, stt_started, "transcribed")
                transcript = text.strip()
                if not transcript or self._stop.is_set():
                    continue

                self.runtime.emit_event(
                    "voice_transcript",
                    transcript,
                    transcript=transcript,
                )

                normalized = transcript.casefold().strip(" .!?\n")
                active = (
                    self._active_command is not None
                    and not self._active_command.done()
                )

                if active or self.runtime.busy:
                    if normalized in _CANCEL_PHRASES:
                        self.runtime.cancel()
                        self.runtime.emit_event(
                            "voice_cancel_requested",
                            "Cancelling…",
                            transcript=transcript,
                        )
                    else:
                        self.runtime.emit_event(
                            "voice_ignored_busy",
                            "Porter is already working.",
                            transcript=transcript,
                        )
                    continue

                self.runtime.emit_event(
                    "voice_command_accepted",
                    transcript,
                    transcript=transcript,
                )
                command_task = asyncio.create_task(
                    self._submit_voice_command(transcript, utterance_id),
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
