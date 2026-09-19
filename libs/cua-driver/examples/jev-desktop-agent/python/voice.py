from __future__ import annotations

import asyncio
import io
import math
import shutil
import subprocess
import threading
import wave

from contracts import Candidate
from loop import AgentLoop, RunResult
from openrouter_client import DEFAULT_STT_MODEL, OpenRouterClient


class LocalSpeaker:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self.binary = shutil.which("spd-say") or shutil.which("espeak-ng") or shutil.which("espeak")

    def say(self, text: str) -> None:
        print(f"[assistant] {text}")
        if not self.enabled or not self.binary or not text.strip():
            return
        try:
            subprocess.run(
                [self.binary, text[:1200]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except Exception:
            pass


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
                is_speech = rms >= threshold
                if is_speech:
                    speech_blocks += 1
                    silent_after_speech = 0
                    blocks.append(mono)
                elif speech_blocks:
                    silent_after_speech += 1
                    blocks.append(mono)
                    if speech_blocks >= min_speech_blocks and silent_after_speech >= silence_limit:
                        break
                elif index > int(8.0 / self.BLOCK_SECONDS):
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
    ) -> None:
        self.agent = agent
        self.client = client
        self.stt_model = stt_model
        self.language = language
        self.speaker = LocalSpeaker(speak)
        self.microphone = Microphone(device=microphone_device)
        self.allow_foreground = allow_foreground
        self._monitor_stop = threading.Event()
        self._monitor_pause = threading.Event()

    async def _listen_text(
        self,
        *,
        announce: bool = True,
        stop_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
    ) -> str | None:
        if announce:
            self.speaker.say("Listening.")
        wav = await asyncio.to_thread(
            self.microphone.record,
            stop_event=stop_event,
            pause_event=pause_event,
        )
        if not wav:
            return None
        text = await asyncio.to_thread(
            self.client.transcribe_wav,
            wav,
            model=self.stt_model,
            language=self.language,
        )
        if text:
            print(f"[heard] {text}")
        return text or None

    async def _confirm(self, candidate: Candidate) -> bool:
        self._monitor_pause.set()
        await asyncio.sleep(self.microphone.BLOCK_SECONDS * 2)
        self.speaker.say(f"Confirm: {candidate.description} Say yes or no.")
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

    async def _monitor_cancellation(self, cancel_event: asyncio.Event) -> None:
        cancel_words = {
            "cancel",
            "stop",
            "stop now",
            "cancel that",
            "stop that",
            "never mind",
            "nevermind",
        }
        while not self._monitor_stop.is_set() and not cancel_event.is_set():
            if self._monitor_pause.is_set():
                await asyncio.sleep(0.1)
                continue
            try:
                answer = await self._listen_text(
                    announce=False,
                    stop_event=self._monitor_stop,
                    pause_event=self._monitor_pause,
                )
            except Exception:
                return
            if not answer:
                continue
            normalized = answer.casefold().strip(" .!?\n")
            if normalized in cancel_words:
                cancel_event.set()
                self.speaker.say("Cancelling.")
                return

    async def execute_command(self, command: str) -> RunResult:
        cancel_event = asyncio.Event()
        self._monitor_stop.clear()
        self._monitor_pause.clear()
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
            self._monitor_cancellation(cancel_event)
        )
        try:
            return await run_task
        finally:
            self._monitor_stop.set()
            self._monitor_pause.clear()
            try:
                await asyncio.wait_for(monitor_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                monitor_task.cancel()

    async def run_forever(self) -> None:
        self.speaker.say("Voice computer control is ready.")
        while True:
            try:
                command = await self._listen_text()
            except KeyboardInterrupt:
                return
            if not command:
                continue
            normalized = command.casefold().strip(" .!?\n")
            if normalized in {"quit", "exit", "stop listening", "goodbye"}:
                self.speaker.say("Stopping voice control.")
                return
            if normalized in {"cancel", "never mind", "nevermind"}:
                self.speaker.say("Cancelled.")
                continue
            try:
                result = await self.execute_command(command)
            except Exception as error:
                self.speaker.say(f"The command failed: {error}")
                continue
            if result.status == "completed":
                self.speaker.say("Done.")
            else:
                self.speaker.say(f"Stopped with {result.status}. {result.message}")
