from __future__ import annotations

import argparse
import asyncio
import json
import os

from contracts import Candidate
from driver import CuaMcpDriver
from jev_adapter import HierarchicalChooser, chooser_from_env
from loop import AgentLoop, RunResult
from openrouter_client import (
    DEFAULT_REASONING_MODEL,
    DEFAULT_STT_MODEL,
    OpenRouterClient,
)
from perception import NoopPerceiver, OpenRouterVisionPerceiver
from planner import OpenRouterPlanner, PassThroughPlanner
from verifier import ConservativeVerifier, OpenRouterVerifier
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
        "--app",
        help="override the planner and target a matching app/window",
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
        "--planner-model",
        default=os.getenv(
            "JEV_DESKTOP_PLANNER_MODEL",
            DEFAULT_REASONING_MODEL,
        ),
    )
    result.add_argument(
        "--vision-model",
        default=os.getenv(
            "JEV_DESKTOP_VISION_MODEL",
            DEFAULT_REASONING_MODEL,
        ),
    )
    result.add_argument(
        "--verifier-model",
        default=os.getenv(
            "JEV_DESKTOP_VERIFIER_MODEL",
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
    result.add_argument("--no-planner", action="store_true")
    result.add_argument("--no-vision", action="store_true")
    result.add_argument("--no-verifier", action="store_true")
    result.add_argument(
        "--approve-consequential",
        action="store_true",
        help="pre-authorize actions normally requiring confirmation",
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
    result.add_argument("--max-candidates", type=int, default=96)
    result.add_argument("--min-confidence", type=float, default=0.55)
    result.add_argument(
        "--json",
        action="store_true",
        help="print full JSON result",
    )
    return result


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
    if not args.voice and not args.goal:
        raise SystemExit("provide a goal or use --voice")
    try:
        chooser = HierarchicalChooser(
            chooser_from_env(args.provider),
            max_leaf_candidates=32,
            group_size=20,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from None

    openrouter = None
    if os.getenv("OPENROUTER_API_KEY", "").strip():
        openrouter = OpenRouterClient()
    planner = (
        PassThroughPlanner()
        if args.no_planner or openrouter is None
        else OpenRouterPlanner(
            openrouter,
            model=args.planner_model,
        )
    )
    perceiver = (
        NoopPerceiver()
        if args.no_vision or openrouter is None
        else OpenRouterVisionPerceiver(
            openrouter,
            model=args.vision_model,
        )
    )
    verifier = (
        ConservativeVerifier()
        if args.no_verifier or openrouter is None
        else OpenRouterVerifier(
            openrouter,
            model=args.verifier_model,
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

    run_error: Exception | None = None
    result = None
    async with CuaMcpDriver() as driver:
        warnings = await driver.health_warnings()
        for warning in warnings:
            print(f"[preflight] {warning}")

        agent = AgentLoop(
            driver,
            chooser,
            planner=planner,
            verifier=verifier,
            perceiver=perceiver,
            writer=writer,
            max_steps=args.max_steps,
            max_candidates=args.max_candidates,
            min_confidence=args.min_confidence,
        )
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
                    confirm=(
                        _terminal_confirm
                        if args.act
                        else None
                    ),
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
    if not 4 <= args.max_candidates <= 192:
        raise SystemExit(
            "--max-candidates must be between 4 and 192"
        )
    if not 0.0 <= args.min_confidence <= 1.0:
        raise SystemExit(
            "--min-confidence must be between 0 and 1"
        )
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
