from __future__ import annotations

import asyncio
import contextlib
import io
import math
import shutil
import subprocess
import threading
import wave
from collections import deque
from typing import Callable

from contracts import Candidate
from loop import AgentLoop, RunResult
from openrouter_client import DEFAULT_STT_MODEL, OpenRouterClient


class LocalSpeaker:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self.binary = (
            shutil.which("spd-say")
            or shutil.which("espeak-ng")
            or shutil.which("espeak")
        )
        self._process_lock = threading.Lock()
        self._process: subprocess.Popen | None = None

    def show(self, text: str) -> None:
        print(f"[assistant] {text}", flush=True)

    async def say(self, text: str) -> None:
        self.show(text)
        if not self.enabled or not self.binary or not text.strip():
            return
        try:
            await asyncio.to_thread(self._speak_sync, text)
        except Exception:
            pass

    async def interrupt(self) -> None:
        """Stop active local playback without disabling future TTS."""
        await asyncio.to_thread(self._interrupt_sync)

    async def wait_until_idle(self) -> None:
        """Wait until the currently owned playback process has released."""
        await asyncio.to_thread(self._wait_until_idle_sync)

    def _interrupt_sync(self) -> None:
        with self._process_lock:
            process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1.0)
        except Exception:
            pass
        finally:
            with self._process_lock:
                if self._process is process:
                    self._process = None

    def _wait_until_idle_sync(self) -> None:
        with self._process_lock:
            process = self._process
        if process is None:
            return
        try:
            process.wait(timeout=30.0)
        except Exception:
            pass

    def _speak_sync(self, text: str) -> None:
        self._interrupt_sync()
        command = [self.binary]
        if self.binary.endswith("spd-say"):
            command.append("--wait")
        command.append(text[:1200])

        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with self._process_lock:
            self._process = process
        try:
            process.wait(timeout=30.0)
        except subprocess.TimeoutExpired:
            try:
                process.terminate()
                process.wait(timeout=1.0)
            except Exception:
                try:
                    process.kill()
                    process.wait(timeout=1.0)
                except Exception:
                    pass
        finally:
            with self._process_lock:
                if self._process is process:
                    self._process = None


class Microphone:
    RATE = 16000
    CHANNELS = 1
    BLOCK_SECONDS = 0.1

    def __init__(
        self,
        *,
        device=None,
        max_seconds: float = 15.0,
        silence_seconds: float = 1.0,
        min_speech_seconds: float = 0.25,
        base_threshold: float = 0.012,
    ) -> None:
        self.device = device
        self.max_seconds = max_seconds
        self.silence_seconds = silence_seconds
        self.min_speech_seconds = min_speech_seconds
        self.base_threshold = base_threshold
        self._lock = threading.Lock()

    def record(
        self,
        *,
        stop_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
        on_speech_start: Callable[[], None] | None = None,
        on_level: Callable[[float, float], None] | None = None,
        pre_roll_seconds: float = 0.3,
    ) -> bytes | None:
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError as error:
            raise RuntimeError(
                "voice mode requires the optional voice dependencies; run `uv sync --extra voice`"
            ) from error

        blocksize = int(self.RATE * self.BLOCK_SECONDS)
        blocks: list[object] = []
        speech_blocks = 0
        silent_after_speech = 0
        noise_samples: list[float] = []
        pre_roll = deque(
            maxlen=max(1, int(pre_roll_seconds / self.BLOCK_SECONDS))
        )
        speech_started = False
        max_blocks = max(1, int(self.max_seconds / self.BLOCK_SECONDS))
        silence_limit = max(1, int(self.silence_seconds / self.BLOCK_SECONDS))
        min_speech_blocks = max(1, int(self.min_speech_seconds / self.BLOCK_SECONDS))

        with self._lock, sd.InputStream(
            samplerate=self.RATE,
            channels=self.CHANNELS,
            dtype="float32",
            blocksize=blocksize,
            device=self.device,
        ) as stream:
            for index in range(max_blocks):
                if (
                    (stop_event is not None and stop_event.is_set())
                    or (pause_event is not None and pause_event.is_set())
                ):
                    return None
                data, overflowed = stream.read(blocksize)
                if overflowed:
                    continue
                mono = data[:, 0].copy()
                rms = float(math.sqrt(float(np.mean(mono * mono)) + 1e-12))
                if speech_blocks == 0 and len(noise_samples) < 5:
                    noise_samples.append(rms)
                noise = sum(noise_samples) / len(noise_samples) if noise_samples else 0.0
                threshold = max(self.base_threshold, noise * 3.0)
                if on_level is not None:
                    try:
                        on_level(rms, threshold)
                    except Exception:
                        pass
                is_speech = rms >= threshold
                if is_speech:
                    if not speech_started:
                        speech_started = True
                        blocks.extend(pre_roll)
                        pre_roll.clear()
                        if on_speech_start is not None:
                            try:
                                on_speech_start()
                            except Exception:
                                pass
                    speech_blocks += 1
                    silent_after_speech = 0
                    blocks.append(mono)
                elif speech_blocks:
                    silent_after_speech += 1
                    blocks.append(mono)
                    if (
                        speech_blocks >= min_speech_blocks
                        and silent_after_speech >= silence_limit
                    ):
                        break
                else:
                    pre_roll.append(mono)
                    if index > int(8.0 / self.BLOCK_SECONDS):
                        return None

        if speech_blocks < min_speech_blocks or not blocks:
            return None
        samples = np.concatenate(blocks)
        pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.RATE)
            wav.writeframes(pcm)
        return output.getvalue()


class VoiceAssistant:
    def __init__(
        self,
        agent: AgentLoop,
        client: OpenRouterClient,
        *,
        stt_model: str = DEFAULT_STT_MODEL,
        language: str | None = None,
        speak: bool = False,
        microphone_device=None,
        allow_foreground: bool = False,
        silence_seconds: float = 0.5,
    ) -> None:
        self.agent = agent
        self.client = client
        self.stt_model = stt_model
        self.language = language
        self.speaker = LocalSpeaker(speak)
        self.microphone = Microphone(
            device=microphone_device,
            silence_seconds=silence_seconds,
        )
        self.allow_foreground = allow_foreground
        self._monitor_stop = threading.Event()
        self._monitor_pause = threading.Event()
        # In-flight transcriptions are tracked so they can be drained at final
        # shutdown, but never waited on between commands.
        self._transcriptions: set[asyncio.Future] = set()
        self._session_active = False

    async def _transcribe(self, wav: bytes) -> str | None:
        task = asyncio.ensure_future(
            asyncio.to_thread(
                self.client.transcribe_wav,
                wav,
                model=self.stt_model,
                language=self.language,
            )
        )
        self._transcriptions.add(task)
        task.add_done_callback(self._forget_transcription)
        # Shield so cancelling the monitor does not cancel the in-flight
        # transcription; it is tracked and drained only at final shutdown.
        return await asyncio.shield(task)

    def _forget_transcription(self, task: asyncio.Future) -> None:
        """Drop a finished transcription without leaking an unretrieved error.

        An orphaned task (for example one whose monitor was cancelled before it
        finished) would otherwise log "exception was never retrieved". Normal
        awaiting still receives the result, because the exception is only
        consumed from this callback, which runs after completion.
        """
        self._transcriptions.discard(task)
        if task.cancelled():
            return
        task.exception()

    async def _drain_transcriptions(self) -> None:
        pending = [
            task for task in self._transcriptions if not task.done()
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _listen_text(
        self,
        *,
        announce: bool = True,
        stop_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
    ) -> str | None:
        if announce:
            self.speaker.show("Listening.")
        wav = await asyncio.to_thread(
            self.microphone.record,
            stop_event=stop_event,
            pause_event=pause_event,
        )
        if not wav:
            return None
        text = await self._transcribe(wav)
        # A monitor transcription can outlive a cancelled task; ignore its
        # result once the monitor is stopped or paused, before printing it.
        if (stop_event is not None and stop_event.is_set()) or (
            pause_event is not None and pause_event.is_set()
        ):
            return None
        if text:
            print(f"[heard] {text}")
        return text or None

    async def _confirm(self, candidate: Candidate) -> bool:
        self._monitor_pause.set()
        await asyncio.sleep(self.microphone.BLOCK_SECONDS * 2)
        await self.speaker.say(
            f"Confirm: {candidate.description} Say yes or no."
        )
        try:
            for _ in range(2):
                answer = await self._listen_text()
                if not answer:
                    continue
                normalized = answer.casefold().strip(" .!?\n")
                if normalized in {"yes", "yeah", "yep", "confirm", "confirmed", "proceed", "do it"}:
                    return True
                if normalized in {"no", "nope", "cancel", "stop", "don't", "do not"}:
                    return False
            return False
        finally:
            self._monitor_pause.clear()

    async def _monitor_cancellation(
        self,
        cancel_event: asyncio.Event,
        monitor_stop: threading.Event,
        monitor_pause: threading.Event,
    ) -> None:
        cancel_words = {
            "cancel",
            "stop",
            "stop now",
            "cancel that",
            "stop that",
            "never mind",
            "nevermind",
        }
        while not monitor_stop.is_set() and not cancel_event.is_set():
            if monitor_pause.is_set():
                await asyncio.sleep(0.1)
                continue
            try:
                answer = await self._listen_text(
                    announce=False,
                    stop_event=monitor_stop,
                    pause_event=monitor_pause,
                )
            except Exception:
                return
            if not answer:
                continue
            if monitor_stop.is_set() or monitor_pause.is_set():
                # Stale result from a transcription that outlived this monitor.
                return
            normalized = answer.casefold().strip(" .!?\n")
            if normalized in cancel_words:
                cancel_event.set()
                await self.speaker.say("Cancelling.")
                return

    async def execute_command(self, command: str) -> RunResult:
        cancel_event = asyncio.Event()
        # Fresh per-command monitor events; a cancelled monitor's tokens are
        # never reused or cleared for the next command.
        monitor_stop = threading.Event()
        monitor_pause = threading.Event()
        self._monitor_stop = monitor_stop
        self._monitor_pause = monitor_pause
        run_task = asyncio.create_task(
            self.agent.run(
                command,
                act=True,
                confirm=self._confirm,
                allow_foreground=self.allow_foreground,
                cancel_event=cancel_event,
            )
        )
        monitor_task = asyncio.create_task(
            self._monitor_cancellation(
                cancel_event,
                monitor_stop,
                monitor_pause,
            )
        )
        try:
            return await run_task
        finally:
            monitor_stop.set()
            monitor_pause.clear()
            monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor_task
            if not self._session_active:
                # One-shot voice: this is final shutdown, so drain any in-flight
                # transcription before the shared client is closed.
                await self._drain_transcriptions()

    async def run_forever(self) -> None:
        self._session_active = True
        try:
            await self.speaker.say("Voice computer control is ready.")
            while True:
                try:
                    command = await self._listen_text()
                except KeyboardInterrupt:
                    return
                if not command:
                    continue
                normalized = command.casefold().strip(" .!?\n")
                if normalized in {"quit", "exit", "stop listening", "goodbye"}:
                    await self.speaker.say("Stopping voice control.")
                    return
                if normalized in {"cancel", "never mind", "nevermind"}:
                    await self.speaker.say("Cancelled.")
                    continue
                try:
                    result = await self.execute_command(command)
                except Exception as error:
                    await self.speaker.say(f"The command failed: {error}")
                    continue
                if result.status == "completed":
                    await self.speaker.say("Done.")
                else:
                    await self.speaker.say(
                        f"Stopped with {result.status}. {result.message}"
                    )
        finally:
            self._session_active = False
            # Drain only at final voice shutdown, never between commands.
            await self._drain_transcriptions()
