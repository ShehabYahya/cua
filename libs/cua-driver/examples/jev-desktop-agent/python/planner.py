from __future__ import annotations

import json
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
    async def plan(
        self,
        goal: str,
        *,
        desktop: DesktopOverview | None = None,
        recent_context: tuple[str, ...] = (),
    ) -> Plan:
        return Plan(goal=goal, steps=(PlanStep(goal=goal, completion=goal),))

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

        return await asyncio.to_thread(self._plan_sync, goal, desktop, recent_context)

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
        return Plan(goal=goal, steps=tuple(steps))

    async def repair_step(
        self,
        *,
        original_goal: str,
        current: PlanStep,
        observation: Observation,
        history: list[StepRecord],
    ) -> PlanStep:
        import asyncio

        return await asyncio.to_thread(
            self._repair_sync, original_goal, current, observation, history
        )

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
Do not output tool names, coordinates, selectors, or shell commands. Keep the replacement consistent with the original user goal.
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
