from __future__ import annotations

import json
import re
from typing import Protocol

from contracts import DesktopOverview, Observation, Plan, PlanStep, StepRecord
from openrouter_client import DEFAULT_REASONING_MODEL, OpenRouterClient


class Planner(Protocol):
    async def plan(
        self,
        goal: str,
        *,
        desktop: DesktopOverview | None = None,
        recent_context: tuple[str, ...] = (),
    ) -> Plan: ...

    async def repair_step(
        self,
        *,
        original_goal: str,
        current: PlanStep,
        observation: Observation,
        history: list[StepRecord],
    ) -> PlanStep: ...


class PassThroughPlanner:
    @staticmethod
    def _infer_app(
        goal: str,
        desktop: DesktopOverview | None,
    ) -> str | None:
        if desktop is None:
            return None
        normalized = goal.casefold()
        names: list[str] = []
        for raw in (*desktop.windows, *desktop.apps):
            name = raw.get("app_name") or raw.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
        unique = sorted(set(names), key=len, reverse=True)
        for name in unique:
            lowered = name.casefold()
            if lowered in normalized:
                return name
            tokens = [
                token
                for token in re.findall(r"[a-z0-9]+", lowered)
                if len(token) >= 4
            ]
            if any(
                re.search(rf"\b{re.escape(token)}\b", normalized)
                for token in tokens
            ):
                return name
        return None

    async def plan(
        self,
        goal: str,
        *,
        desktop: DesktopOverview | None = None,
        recent_context: tuple[str, ...] = (),
    ) -> Plan:
        app = self._infer_app(goal, desktop)
        return Plan(
            goal=goal,
            steps=(
                PlanStep(
                    goal=goal,
                    app=app,
                    completion=goal,
                ),
            ),
        )

    async def repair_step(
        self,
        *,
        original_goal: str,
        current: PlanStep,
        observation: Observation,
        history: list[StepRecord],
    ) -> PlanStep:
        return current


class OpenRouterPlanner:
    def __init__(
        self,
        client: OpenRouterClient,
        *,
        model: str = DEFAULT_REASONING_MODEL,
        max_steps: int = 10,
    ) -> None:
        self.client = client
        self.model = model
        self.max_steps = max_steps

    async def plan(
        self,
        goal: str,
        *,
        desktop: DesktopOverview | None = None,
        recent_context: tuple[str, ...] = (),
    ) -> Plan:
        import asyncio

        try:
            return await asyncio.to_thread(
                self._plan_sync,
                goal,
                desktop,
                recent_context,
            )
        except Exception:
            return Plan(
                goal=goal,
                steps=(PlanStep(goal=goal, completion=goal),),
            )

    def _plan_sync(
        self,
        goal: str,
        desktop: DesktopOverview | None,
        recent_context: tuple[str, ...],
    ) -> Plan:
        desktop_json = json.dumps(desktop.compact() if desktop else {}, ensure_ascii=False)
        context_json = json.dumps(list(recent_context[-8:]), ensure_ascii=False)
        prompt = f"""User goal: {goal}

Visible desktop/apps: {desktop_json}
Recent session context: {context_json}

Return JSON only with this shape:
{{"steps":[{{"goal":"...","app":null,"text":null,"completion":"observable condition"}}]}}

Rules:
- Produce 1 to {self.max_steps} ordered GUI subgoals.
- app is the application name to target if known, otherwise null.
- text is the exact string that should be typed for that subgoal, otherwise null.
- Preserve user-quoted text exactly.
- You may generate ordinary message/search text when the user requested it, but never passwords, OTPs, payment-card data, recovery codes, or secret keys.
- completion must describe a visible/readable postcondition.
- Do not output coordinates, selectors, shell commands, tool names, or mouse/keyboard instructions.
- Prefer using already-open applications shown in the desktop context.
- Do NOT create subgoals whose only purpose is bringing/focusing/activating a window or app. The harness selects the target app and Driver handles foreground escalation only when needed.
- Do NOT create a separate final verification/confirmation/"stop" subgoal. Put the observable success condition on the mutating subgoal that should produce it.
- If later steps already target an app, do not add a separate "open/launch/start <app>" subgoal; the harness launches a missing target app automatically.
"""
        body = self.client.chat_json(
            system="You plan bounded desktop GUI work. Return valid JSON only.",
            prompt=prompt,
            model=self.model,
            image_path=desktop.screenshot_path if desktop else None,
            max_tokens=1400,
        )
        return self._parse_plan(goal, body)

    def _parse_plan(self, goal: str, body: object) -> Plan:
        if not isinstance(body, dict) or not isinstance(body.get("steps"), list):
            raise ValueError("planner returned no steps")
        steps: list[PlanStep] = []
        for raw in body["steps"][: self.max_steps]:
            if not isinstance(raw, dict):
                continue
            subgoal = raw.get("goal")
            if not isinstance(subgoal, str) or not subgoal.strip():
                continue
            app = raw.get("app")
            text = raw.get("text")
            completion = raw.get("completion")
            steps.append(
                PlanStep(
                    goal=subgoal.strip(),
                    app=app.strip() if isinstance(app, str) and app.strip() else None,
                    text=text if isinstance(text, str) and text else None,
                    completion=(
                        completion.strip()
                        if isinstance(completion, str) and completion.strip()
                        else subgoal.strip()
                    ),
                )
            )
        if not steps:
            steps = [PlanStep(goal=goal, completion=goal)]
        steps = self._normalize_steps(steps)
        if not steps:
            steps = [PlanStep(goal=goal, completion=goal)]
        return Plan(goal=goal, steps=tuple(steps))

    @staticmethod
    def _normalize_steps(steps: list[PlanStep]) -> list[PlanStep]:
        if len(steps) <= 1:
            return steps

        def words(text: str) -> str:
            return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))

        normalized: list[PlanStep] = []
        for index, step in enumerate(steps):
            goal_text = words(step.goal)

            focus_only = (
                any(
                    phrase in goal_text
                    for phrase in (
                        "bring to foreground",
                        "bring the window to the foreground",
                        "bring window to foreground",
                        "focus the window",
                        "focus window",
                        "activate the window",
                        "activate window",
                        "make the window active",
                        "make window active",
                    )
                )
                or (
                    "foreground" in goal_text
                    and any(word in goal_text for word in ("window", "app", "firefox", "chrome"))
                )
            )
            if focus_only:
                continue

            verify_only = (
                index == len(steps) - 1
                and (
                    goal_text.startswith("confirm ")
                    or goal_text.startswith("verify ")
                    or goal_text.startswith("check ")
                    or " and stop" in goal_text
                    or goal_text.startswith("stop when ")
                )
            )
            if verify_only and normalized:
                previous = normalized[-1]
                normalized[-1] = PlanStep(
                    goal=previous.goal,
                    app=previous.app,
                    text=previous.text,
                    completion=step.completion or previous.completion,
                )
                continue

            launch_only = (
                index < len(steps) - 1
                and step.app
                and re.fullmatch(
                    r"(?:open|launch|start)(?: the)? "
                    + re.escape(step.app.casefold())
                    + r"(?: window)?",
                    goal_text,
                )
                is not None
                and any(
                    later.app
                    and later.app.casefold() == step.app.casefold()
                    for later in steps[index + 1 :]
                )
            )
            if launch_only:
                continue

            normalized.append(step)
        return normalized

    async def repair_step(
        self,
        *,
        original_goal: str,
        current: PlanStep,
        observation: Observation,
        history: list[StepRecord],
    ) -> PlanStep:
        import asyncio

        try:
            return await asyncio.to_thread(
                self._repair_sync,
                original_goal,
                current,
                observation,
                history,
            )
        except Exception:
            return current

    def _repair_sync(
        self,
        original_goal: str,
        current: PlanStep,
        observation: Observation,
        history: list[StepRecord],
    ) -> PlanStep:
        prompt = f"""Original goal: {original_goal}
Current subgoal: {json.dumps(current.__dict__, ensure_ascii=False)}
Current observation: {json.dumps(observation.compact(limit=70), ensure_ascii=False)}
Recent attempts: {json.dumps([item.compact() for item in history[-6:]], ensure_ascii=False)}

The current subgoal is stalled or uncertain. Return one replacement GUI subgoal as JSON:
{{"goal":"...","app":null,"text":null,"completion":"observable condition"}}
Do not output tool names, coordinates, selectors, or shell commands. Keep the replacement consistent with the original user goal. Do not create a focus/foreground-only replacement; Driver escalation owns foregrounding.
"""
        body = self.client.chat_json(
            system="Repair one bounded desktop GUI subgoal. Return valid JSON only.",
            prompt=prompt,
            model=self.model,
            image_path=observation.screenshot_path,
            max_tokens=700,
        )
        if not isinstance(body, dict) or not isinstance(body.get("goal"), str):
            return current
        return PlanStep(
            goal=body["goal"].strip() or current.goal,
            app=(body.get("app") or current.app)
            if isinstance(body.get("app"), str)
            else current.app,
            text=body.get("text") if isinstance(body.get("text"), str) else current.text,
            completion=(
                body.get("completion").strip()
                if isinstance(body.get("completion"), str)
                and body.get("completion").strip()
                else current.completion
            ),
        )
