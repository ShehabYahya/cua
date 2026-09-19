from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

from contracts import Candidate
from driver import CuaMcpDriver
from jev_adapter import chooser_from_env
from loop import AgentLoop, RunResult
from openrouter_client import (
    DEFAULT_REASONING_MODEL,
    DEFAULT_STT_MODEL,
    OpenRouterClient,
)
from perception import NoopPerceiver, OpenRouterVisionPerceiver
from voice import VoiceAssistant
from writer import OpenRouterWriter


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Jev + Cua general desktop agent"
    )
    result.add_argument(
        "goal",
        nargs="?",
        help="natural-language desktop goal",
    )
    result.add_argument(
        "--check",
        action="store_true",
        help="run Cua desktop-agent preflight without requiring model credentials",
    )
    result.add_argument(
        "--app",
        help="target a matching app/window",
    )
    result.add_argument(
        "--provider",
        choices=["auto", "openrouter", "typesafe"],
        default="auto",
        help="Jev provider; auto prefers OPENROUTER_API_KEY",
    )
    result.add_argument(
        "--act",
        action="store_true",
        help="execute actions; default is dry-run",
    )
    result.add_argument(
        "--voice",
        action="store_true",
        help="continuous microphone command mode",
    )
    result.add_argument(
        "--speak",
        action="store_true",
        help="speak voice-mode responses through speech-dispatcher/espeak",
    )
    result.add_argument(
        "--voice-language",
        help="ISO-639-1 STT language hint, e.g. en or ar",
    )
    result.add_argument(
        "--voice-silence",
        type=float,
        default=0.5,
        help=(
            "seconds of trailing silence that ends a spoken command "
            "(default 0.5)"
        ),
    )
    result.add_argument(
        "--stt-model",
        default=os.getenv(
            "JEV_DESKTOP_STT_MODEL",
            DEFAULT_STT_MODEL,
        ),
    )
    result.add_argument(
        "--mic",
        type=int,
        help="sounddevice microphone index",
    )
    result.add_argument(
        "--vision-model",
        default=os.getenv(
            "JEV_DESKTOP_VISION_MODEL",
            DEFAULT_REASONING_MODEL,
        ),
    )
    result.add_argument(
        "--writer-model",
        default=os.getenv(
            "JEV_DESKTOP_WRITER_MODEL",
            DEFAULT_REASONING_MODEL,
        ),
    )
    result.add_argument("--no-vision", action="store_true")
    result.add_argument(
        "--download-root",
        default=os.getenv("JEV_DESKTOP_DOWNLOAD_ROOT"),
        help=(
            "approved absolute directory for typed browser downloads; "
            "defaults to ~/Downloads when that directory exists"
        ),
    )
    result.add_argument(
        "--confirm-actions",
        action="store_true",
        help=(
            "opt in to the harness policy/confirmation gate: filter "
            "sensitive fields and ask before consequential actions"
        ),
    )
    result.add_argument(
        "--approve-consequential",
        action="store_true",
        help=(
            "with --confirm-actions, pre-authorize actions that would "
            "otherwise require confirmation"
        ),
    )
    result.add_argument(
        "--allow-foreground",
        action="store_true",
        help=(
            "allow Driver-requested foreground escalation after "
            "background refusal"
        ),
    )
    result.add_argument("--max-steps", type=int, default=30)
    result.add_argument("--max-candidates", type=int, default=32)
    result.add_argument(
        "--quiet",
        action="store_true",
        help="suppress live progress messages",
    )
    result.add_argument(
        "--json",
        action="store_true",
        help="print full JSON result",
    )
    return result


def _progress_printer(message: str) -> None:
    print(f"[agent] {message}", file=sys.stderr, flush=True)


async def _terminal_confirm(candidate: Candidate) -> bool:
    prompt = (
        f"Confirm consequential action: {candidate.description} [y/N] "
    )
    answer = await asyncio.to_thread(input, prompt)
    return answer.strip().casefold() in {"y", "yes"}


def _result_json(result: RunResult) -> dict:
    return {
        "status": result.status,
        "message": result.message,
        "completed_subgoals": result.completed_subgoals,
        "plan": [step.__dict__ for step in result.plan.steps],
        "steps": [record.__dict__ for record in result.steps],
    }


async def main_async(args: argparse.Namespace) -> int:
    if args.check:
        async with CuaMcpDriver() as driver:
            warnings = await driver.health_warnings()
            limitations = driver.capability_limitations()
            overview = await driver.desktop_overview()
            payload = {
                "status": "ok" if not warnings else "degraded",
                "warnings": list(warnings),
                "limitations": list(limitations),
                "capabilities": driver.capability_summary(),
                "visible_windows": len(overview.windows),
                "known_apps": len(overview.apps),
                "desktop_screenshot": bool(overview.screenshot_path),
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0 if not warnings else 2

    if not args.voice and not args.goal:
        raise SystemExit("provide a goal, use --voice, or use --check")

    async with contextlib.AsyncExitStack() as stack:
        try:
            chooser = chooser_from_env(args.provider)
        except ValueError as error:
            raise SystemExit(str(error)) from None
        stack.callback(chooser.close)

        openrouter = None
        if os.getenv("OPENROUTER_API_KEY", "").strip():
            try:
                openrouter = OpenRouterClient()
            except ValueError as error:
                raise SystemExit(str(error)) from None
            stack.callback(openrouter.close)

        perceiver = (
            NoopPerceiver()
            if args.no_vision or openrouter is None
            else OpenRouterVisionPerceiver(
                openrouter,
                model=args.vision_model,
            )
        )
        writer = (
            None
            if openrouter is None
            else OpenRouterWriter(
                openrouter,
                model=args.writer_model,
            )
        )

        driver = await stack.enter_async_context(CuaMcpDriver())
        agent = AgentLoop(
            driver,
            chooser,
            writer=writer,
            perceiver=perceiver,
            max_steps=args.max_steps,
            max_candidates=args.max_candidates,
            download_root=(
                args.download_root
                or (
                    str(Path.home() / "Downloads")
                    if (Path.home() / "Downloads").is_dir()
                    else None
                )
            ),
            progress=None if args.quiet else _progress_printer,
            enforce_policy=args.confirm_actions,
        )

        run_error: Exception | None = None
        result = None
        try:
            if args.voice:
                if openrouter is None:
                    raise RuntimeError(
                        "voice mode requires OPENROUTER_API_KEY "
                        "for transcription"
                    )
                voice = VoiceAssistant(
                    agent,
                    openrouter,
                    stt_model=args.stt_model,
                    language=args.voice_language,
                    speak=args.speak,
                    microphone_device=args.mic,
                    allow_foreground=args.allow_foreground,
                    silence_seconds=args.voice_silence,
                )
                if args.goal:
                    result = await voice.execute_command(args.goal)
                else:
                    await voice.run_forever()
                    return 0
            else:
                result = await agent.run(
                    args.goal,
                    app=args.app,
                    act=args.act,
                    approve_consequential=(
                        args.approve_consequential
                    ),
                    allow_foreground=args.allow_foreground,
                    confirm=_terminal_confirm,
                )
        except Exception as error:
            run_error = error

        if run_error is not None:
            raise SystemExit(f"error: {run_error}") from None
        assert result is not None
        payload = _result_json(result)
        if args.json:
            print(
                json.dumps(
                    payload,
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(f"{result.status}: {result.message}")
            if result.steps:
                last = result.steps[-1]
                print(
                    f"last decision: {last.selected_id} "
                    f"({last.confidence:.0%}) — {last.description}"
                )
        return 0 if result.status in {"completed", "dry_run"} else 2


def main() -> int:
    args = parser().parse_args()
    if args.max_steps < 1:
        raise SystemExit("--max-steps must be at least 1")
    if not 4 <= args.max_candidates <= 32:
        raise SystemExit(
            "--max-candidates must be between 4 and 32"
        )
    if not 0.1 <= args.voice_silence <= 10.0:
        raise SystemExit(
            "--voice-silence must be between 0.1 and 10.0 seconds"
        )
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
