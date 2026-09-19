from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

CHAT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
TRANSCRIPTION_ENDPOINT = "https://openrouter.ai/api/v1/audio/transcriptions"
DEFAULT_REASONING_MODEL = "openrouter/auto"
DEFAULT_STT_MODEL = "openai/whisper-1"


def _extract_json(text: str) -> Any:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass
    candidates: list[tuple[int, int]] = []
    for opener, closer in (("{", "}"), ("[", "]")):
        start = value.find(opener)
        end = value.rfind(closer)
        if start >= 0 and end > start:
            candidates.append((start, end + 1))
    for start, end in sorted(candidates):
        try:
            return json.loads(value[start:end])
        except json.JSONDecodeError:
            continue
    raise ValueError("model did not return valid JSON")


class OpenRouterClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 45.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.api_key = (api_key or os.getenv("OPENROUTER_API_KEY", "")).strip()
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is required")
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def _post(self, endpoint: str, payload: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener(request, timeout=timeout or self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"OpenRouter HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            raise RuntimeError("OpenRouter request failed") from None
        if not isinstance(body, dict):
            raise RuntimeError("OpenRouter returned a malformed response")
        return body

    def chat_json(
        self,
        *,
        system: str,
        prompt: str,
        model: str = DEFAULT_REASONING_MODEL,
        image_path: str | None = None,
        max_tokens: int = 1200,
        temperature: float = 0.0,
    ) -> Any:
        if image_path:
            path = Path(image_path)
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            content: Any = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
            ]
        else:
            content = prompt
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "provider": {
                "data_collection": "deny",
                "zdr": True,
                "allow_fallbacks": True,
            },
        }
        body = self._post(CHAT_ENDPOINT, payload)
        try:
            content_out = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("OpenRouter chat response has no message content") from None
        if isinstance(content_out, list):
            text_parts = [
                part.get("text", "")
                for part in content_out
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            content_out = "\n".join(text_parts)
        if not isinstance(content_out, str):
            raise RuntimeError("OpenRouter chat response content is not text")
        return _extract_json(content_out)

    def transcribe_wav(
        self,
        wav_bytes: bytes,
        *,
        model: str = DEFAULT_STT_MODEL,
        language: str | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "input_audio": {
                "data": base64.b64encode(wav_bytes).decode("ascii"),
                "format": "wav",
            },
        }
        if language:
            payload["language"] = language
        body = self._post(TRANSCRIPTION_ENDPOINT, payload, timeout=60.0)
        text = body.get("text")
        if not isinstance(text, str):
            raise RuntimeError("OpenRouter transcription response has no text")
        return text.strip()
