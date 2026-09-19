from __future__ import annotations

import argparse
import asyncio
import json

from driver import CuaMcpDriver
from jev_adapter import chooser_from_env
from loop import AgentLoop


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Experimental Jev desktop loop on Cua Driver"
    )
    result.add_argument("goal", help="natural-language desktop goal")
    result.add_argument("--app", help="limit observation to a matching app/window")
    result.add_argument(
        "--provider",
        choices=["auto", "openrouter", "typesafe"],
        default="auto",
        help="Jev provider; auto prefers OPENROUTER_API_KEY when present",
    )
    result.add_argument(
        "--act",
        action="store_true",
        help="execute selected actions; default is dry-run",
    )
    result.add_argument("--max-steps", type=int, default=24)
    result.add_argument("--min-confidence", type=float, default=0.55)
    return result


async def main_async(args: argparse.Namespace) -> int:
    try:
        chooser = chooser_from_env(args.provider)
    except ValueError as error:
        raise SystemExit(str(error)) from None

    run_error: Exception | None = None
    result = None
    async with CuaMcpDriver() as driver:
        agent = AgentLoop(
            driver,
            chooser,
            max_steps=args.max_steps,
            min_confidence=args.min_confidence,
        )
        try:
            result = await agent.run(args.goal, app=args.app, act=args.act)
        except Exception as error:
            # Leave the MCP context normally so AnyIO does not wrap an ordinary
            # agent error in a noisy TaskGroup ExceptionGroup.
            run_error = error

    if run_error is not None:
        raise SystemExit(f"error: {run_error}") from None
    assert result is not None
    print(
        json.dumps(
            {
                "status": result.status,
                "message": result.message,
                "steps": [record.__dict__ for record in result.steps],
            },
            indent=2,
        )
    )
    return 0 if result.status in {"dry_run", "abstained", "uncertain"} else 1


def main() -> int:
    args = parser().parse_args()
    if args.max_steps < 1:
        raise SystemExit("--max-steps must be at least 1")
    if not 0.0 <= args.min_confidence <= 1.0:
        raise SystemExit("--min-confidence must be between 0 and 1")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
