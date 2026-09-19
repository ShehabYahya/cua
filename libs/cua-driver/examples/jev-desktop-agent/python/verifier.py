from __future__ import annotations

import json
from typing import Protocol

from contracts import Observation, PlanStep, StepRecord, Verification
from openrouter_client import DEFAULT_REASONING_MODEL, OpenRouterClient


class GoalVerifier(Protocol):
    async def verify(
        self,
        *,
        original_goal: str,
        step: PlanStep,
        observation: Observation,
        history: list[StepRecord],
    ) -> Verification: ...


class ConservativeVerifier:
    async def verify(
        self,
        *,
        original_goal: str,
        step: PlanStep,
        observation: Observation,
        history: list[StepRecord],
    ) -> Verification:
        return Verification(False, 0.0, "no generic completion verifier configured")


class OpenRouterVerifier:
    def __init__(
        self,
        client: OpenRouterClient,
        *,
        model: str = DEFAULT_REASONING_MODEL,
        threshold: float = 0.74,
    ) -> None:
        self.client = client
        self.model = model
        self.threshold = threshold

    async def verify(
        self,
        *,
        original_goal: str,
        step: PlanStep,
        observation: Observation,
        history: list[StepRecord],
    ) -> Verification:
        import asyncio

        try:
            raw = await asyncio.to_thread(
                self._verify_sync,
                original_goal,
                step,
                observation,
                history,
            )
        except Exception as error:
            return Verification(False, 0.0, f"verifier unavailable: {error}")
        return raw

    def _verify_sync(
        self,
        original_goal: str,
        step: PlanStep,
        observation: Observation,
        history: list[StepRecord],
    ) -> Verification:
        completion = step.completion or step.goal
        prompt = f"""Original goal: {original_goal}
Current subgoal: {step.goal}
Completion condition: {completion}
Current semantic/visual state: {json.dumps(observation.compact(limit=100), ensure_ascii=False)}
Recent actions: {json.dumps([item.compact() for item in history[-8:]], ensure_ascii=False)}

Judge only whether the completion condition is visibly/readably true now. An action having been attempted is not proof. Return JSON only: {{"done":true,"confidence":0.0,"reason":"brief evidence"}}. If evidence is ambiguous or unavailable, done must be false.
"""
        body = self.client.chat_json(
            system="Verify a computer-use postcondition from current evidence. Return valid JSON only.",
            prompt=prompt,
            model=self.model,
            image_path=observation.screenshot_path,
            max_tokens=500,
        )
        if not isinstance(body, dict):
            return Verification(False, 0.0, "malformed verifier response")
        done = body.get("done") is True
        try:
            confidence = float(body.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        reason = body.get("reason") if isinstance(body.get("reason"), str) else ""
        return Verification(
            done and confidence >= self.threshold,
            confidence,
            reason[:600],
        )


def state_changed(before: Observation, after: Observation) -> bool:
    return before.fingerprint() != after.fingerprint()
