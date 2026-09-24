from __future__ import annotations

import asyncio
import httpx
import json
import math
import os
import re
import urllib.error
import urllib.request
from dataclasses import replace
from typing import Any, Callable

from confidence import assess_decision
from contracts import Candidate, Decision, Observation, StepRecord

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
OPENROUTER_MODEL = "~typesafe/jev-latest"


def _criteria(candidates: list[Candidate]) -> dict[str, str]:
    criteria = {candidate.id: candidate.description for candidate in candidates}
    if len(criteria) != len(candidates):
        raise ValueError("candidate table contains duplicate IDs")
    return criteria


def _history(history: list[StepRecord]) -> list[dict[str, Any]]:
    return [
        {
            "subgoal": item.subgoal,
            "selected_id": item.selected_id,
            "description": item.description,
            "confidence": round(item.confidence, 4),
            "executed": item.executed,
            "outcome": item.outcome,
            "effect": item.effect,
            "reason": item.reason,
        }
        for item in history[-8:]
    ]


def _state(
    goal: str,
    observation: Observation,
    history: list[StepRecord],
    candidates: list[Candidate],
) -> dict[str, Any]:
    # Show the state of the controls Jev can actually choose, even when they
    # occur after the menu tree. Arguments remain local; compact() masks values.
    from policy import field_is_sensitive

    indices: set[int] = set()
    refs: set[str] = set()

    def remember_targets(candidate: Candidate) -> None:
        index = candidate.arguments.get("element_index")
        if isinstance(index, int):
            indices.add(index)
        ref = candidate.arguments.get("ref")
        if isinstance(ref, str):
            refs.add(ref)
        for step in candidate.steps:
            remember_targets(step)

    for candidate in candidates:
        remember_targets(candidate)

    elements = tuple(
        sorted(
            observation.elements,
            key=lambda e: (
                e.index not in indices and e.browser_ref not in refs,
                e.focused is not True and e.selected is not True,
            ),
        )
    )

    # Ordinary editable text is offered as bounded semantic UI state so a
    # decision can rewrite existing content; it is never an executable argument.
    editable_roles = {
        "edit",
        "entry",
        "textbox",
        "textarea",
        "searchfield",
        "combobox",
        "textfield",
        "textentry",
    }

    def role_key(role: str) -> str:
        value = re.sub(r"[^a-z]", "", role.casefold())
        return value[2:] if value.startswith("ax") else value

    editable = [
        element
        for element in observation.elements
        if "password" not in role_key(element.role)
        and "secure" not in role_key(element.role)
        and not field_is_sensitive(element.label)
        and (
            role_key(element.role) in editable_roles
            or any(
                "type" in action.casefold() or "edit" in action.casefold()
                for action in element.actions
            )
        )
    ]
    # Focused fields first, then candidate targets, then other ordinary fields.
    editable.sort(
        key=lambda element: (
            0 if element.focused is True else 1,
            0 if element.index in indices or element.browser_ref in refs else 1,
        )
    )
    observed_field_text = [
        {
            "index": element.index,
            "label": element.label,
            "value": (element.value or "")[:2000],
        }
        for element in editable[:8]
    ]

    return {
        "goal": goal,
        "observation": replace(observation, elements=elements).compact(),
        "observed_field_text": observed_field_text,
        "history": _history(history),
    }


class TypeSafeChooser:
    """Direct TypeSafe route. Executable arguments stay local."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    async def choose(
        self,
        *,
        goal: str,
        observation: Observation,
        candidates: list[Candidate],
        history: list[StepRecord],
    ) -> Decision:
        return await asyncio.to_thread(
            self._choose_sync,
            goal,
            observation,
            candidates,
            history,
        )

    def _choose_sync(
        self,
        goal: str,
        observation: Observation,
        candidates: list[Candidate],
        history: list[StepRecord],
    ) -> Decision:
        from typesafe_sdk import Choice, TypeSafeClient

        criteria = _criteria(candidates)
        request = {
            "state": _state(goal, observation, history, candidates),
            "questions": {
                "driver_action": Choice(
                    instructions=(
                        "Select exactly one supplied candidate ID for the next desktop step. "
                        "Choose done only when the complete current goal is already visibly "
                        "satisfied, never because only an intermediate clause is complete. "
                        "Choose reobserve only when the observation is stale, degraded, truncated, "
                        "or genuinely missing evidence that a fresh capture could provide; do not "
                        "use reobserve as a generic hesitation when actionable candidates are present."
                    ),
                    criteria=criteria,
                )
            },
        }

        if self._client is None:
            self._client = TypeSafeClient()
        response = self._client.system_one(**request)

        answer = response.choices["driver_action"]
        if answer.choice not in criteria:
            raise ValueError(f"Jev selected unknown candidate: {answer.choice}")
        confidence = float(answer.confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("Jev returned invalid confidence")

        probabilities: dict[str, float] = {}
        for candidate_id, raw in answer.probabilities.items():
            if candidate_id not in criteria:
                raise ValueError(f"Jev scored unknown candidate: {candidate_id}")
            value = float(raw)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError("Jev returned invalid probability")
            probabilities[candidate_id] = value

        model = getattr(response, "model", None)
        return Decision(
            selected_id=str(answer.choice),
            confidence=confidence,
            probabilities=probabilities,
            model=model if isinstance(model, str) else None,
        )

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            client = self._client
            self._client = None
            client.close()


class OpenRouterChooser:
    """OpenRouter Decisions API route for TypeSafe Jev."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = OPENROUTER_MODEL,
        timeout: float = 10.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OPENROUTER_API_KEY is empty")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._opener = opener
        # Owned pooled client, used only when no opener was injected. httpx.Client
        # is thread-safe and keeps connections alive for the adapter's lifetime.
        self._http_client = httpx.Client(follow_redirects=True) if opener is None else None

    async def choose(
        self,
        *,
        goal: str,
        observation: Observation,
        candidates: list[Candidate],
        history: list[StepRecord],
    ) -> Decision:
        return await asyncio.to_thread(
            self._choose_sync,
            goal,
            observation,
            candidates,
            history,
        )

    def _choose_sync(
        self,
        goal: str,
        observation: Observation,
        candidates: list[Candidate],
        history: list[StepRecord],
    ) -> Decision:
        criteria = _criteria(candidates)
        payload = {
            "model": self._model,
            "state": _state(goal, observation, history, candidates),
            "questions": {
                "driver_action": {
                    "type": "choice",
                    "instructions": (
                        "Select exactly one supplied candidate ID for the next desktop step. "
                        "Choose done only when the complete current goal is already visibly "
                        "satisfied, never because only an intermediate clause is complete. "
                        "Choose reobserve only when the observation is stale, degraded, truncated, "
                        "or genuinely missing evidence that a fresh capture could provide; do not "
                        "use reobserve as a generic hesitation when actionable candidates are present."
                    ),
                    "criteria": criteria,
                }
            },
            # Keep the decision request on a privacy-restricted OpenRouter route.
            "provider": {
                "data_collection": "deny",
                "zdr": True,
                "allow_fallbacks": False,
            },
        }
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            if self._opener is not None:
                # Injected opener keeps the original urllib-style response/error path.
                request = urllib.request.Request(
                    OPENROUTER_ENDPOINT,
                    data=data,
                    method="POST",
                    headers=headers,
                )
                with self._opener(request, timeout=self._timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
            else:
                response = self._http_client.post(
                    OPENROUTER_ENDPOINT,
                    content=data,
                    headers=headers,
                    timeout=self._timeout,
                )
                response.raise_for_status()
                body = json.loads(response.content.decode("utf-8"))
        except urllib.error.HTTPError as error:
            # Never include the response body: it may echo private request data.
            raise RuntimeError(f"OpenRouter Jev HTTP {error.code}") from None
        except httpx.HTTPStatusError as error:
            # Never include the response body: it may echo private request data.
            raise RuntimeError(
                f"OpenRouter Jev HTTP {error.response.status_code}"
            ) from None
        except (
            urllib.error.URLError,
            httpx.HTTPError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ):
            raise RuntimeError("OpenRouter Jev request failed") from None

        answers = body.get("answers")
        if not isinstance(answers, dict):
            raise ValueError("OpenRouter Jev response has no answers object")
        answer = answers.get("driver_action")
        if not isinstance(answer, dict):
            raise ValueError("OpenRouter Jev response has no driver_action answer")

        selected = answer.get("choice")
        if selected not in criteria:
            raise ValueError(f"Jev selected unknown candidate: {selected}")

        raw_probabilities = answer.get("probabilities")
        if not isinstance(raw_probabilities, dict):
            raise ValueError("OpenRouter Jev returned no probability distribution")
        probabilities: dict[str, float] = {}
        for candidate_id, raw in raw_probabilities.items():
            if candidate_id not in criteria:
                raise ValueError(f"Jev scored unknown candidate: {candidate_id}")
            value = float(raw)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError("OpenRouter Jev returned invalid probability")
            probabilities[candidate_id] = value

        selected_probability = probabilities.get(str(selected))
        if selected_probability is None:
            raise ValueError("OpenRouter Jev omitted the selected candidate probability")
        confidence_raw = answer.get("confidence", selected_probability)
        confidence = float(confidence_raw)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("OpenRouter Jev returned invalid confidence")

        model = body.get("model")
        return Decision(
            selected_id=str(selected),
            confidence=confidence,
            probabilities=probabilities,
            model=model if isinstance(model, str) else self._model,
        )

    def close(self) -> None:
        if self._http_client is not None:
            client = self._http_client
            self._http_client = None
            client.close()


def chooser_from_env(
    provider: str = "auto",
    *,
    model: str | None = None,
) -> TypeSafeChooser | OpenRouterChooser:
    if provider not in {"auto", "openrouter", "typesafe"}:
        raise ValueError("provider must be auto, openrouter, or typesafe")

    if provider in {"auto", "openrouter"}:
        key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if key:
            return OpenRouterChooser(
                key,
                model=(
                    (model or "").strip()
                    or os.getenv("JEV_MODEL", "").strip()
                    or OPENROUTER_MODEL
                ),
            )
        if provider == "openrouter":
            raise ValueError("set OPENROUTER_API_KEY in the environment")

    if provider in {"auto", "typesafe"}:
        if os.getenv("TYPESAFE_API_KEY", "").strip():
            return TypeSafeChooser()
        if provider == "typesafe":
            raise ValueError("set TYPESAFE_API_KEY in the environment")

    raise ValueError(
        "no Jev credential found; set OPENROUTER_API_KEY or TYPESAFE_API_KEY"
    )


_SHORTLIST_STOPWORDS = {
    "a",
    "an",
    "and",
    "the",
    "to",
    "in",
    "into",
    "on",
    "for",
    "with",
    "current",
    "target",
    "window",
    "page",
    "control",
    "button",
    "field",
    "click",
    "activate",
    "open",
    "press",
    "type",
    "put",
    "prepared",
    "text",
    "run",
    "do",
}


def _shortlist_words(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9]+", text.casefold())
        if len(word) > 1 and word not in _SHORTLIST_STOPWORDS
    }


def _relevant_shortlist(
    goal: str,
    candidates: list[Candidate],
    *,
    limit: int,
) -> list[Candidate] | None:
    terminals = [
        candidate
        for candidate in candidates
        if candidate.id in {"done", "reobserve", "abstain"}
    ]
    actions = [
        candidate
        for candidate in candidates
        if candidate.id not in {"done", "reobserve", "abstain"}
    ]
    goal_words = _shortlist_words(goal)
    if not goal_words:
        return None

    ranked: list[tuple[int, int, Candidate]] = []
    for index, candidate in enumerate(actions):
        overlap = len(goal_words & _shortlist_words(candidate.description))
        priority = 1 if candidate.source in {"shortcut", "keyboard"} else 0
        if overlap or priority:
            ranked.append((overlap, priority, candidate))

    if not ranked:
        return None

    ranked.sort(
        key=lambda item: (item[0], item[1]),
        reverse=True,
    )
    room = max(0, limit - len(terminals))
    # These routes were reserved by the builder. Lexical matches in a large
    # menu must not evict typing or keyboard recovery at this second budget.
    reserved = [c for c in actions if c.source in {"shortcut", "keyboard"}]
    reserved += [
        c for c in actions if c.tool in {"type_text", "browser_type"} and c not in reserved
    ][:4]
    selected = reserved[:room]
    selected_ids = {candidate.id for candidate in selected}
    for _, _, candidate in ranked:
        if len(selected) >= room:
            break
        if candidate.id not in selected_ids:
            selected.append(candidate)
            selected_ids.add(candidate.id)

    # Keep a small hedge of the builder's already relevance-sorted actions so
    # synonym mismatches do not make lexical shortlisting brittle.
    for candidate in actions:
        if len(selected) >= room or len(selected) >= 24:
            break
        if candidate.id in selected_ids:
            continue
        selected.append(candidate)
        selected_ids.add(candidate.id)

    if not selected or len(selected) + len(terminals) > limit:
        return None
    return selected + terminals


class HierarchicalChooser:
    """Keep each Jev choice bounded while still considering a large action pool."""

    def __init__(
        self,
        inner,
        *,
        max_leaf_candidates: int = 32,
        group_size: int = 20,
    ) -> None:
        if max_leaf_candidates < 6:
            raise ValueError("max_leaf_candidates is too small")
        if group_size < 2 or group_size > max_leaf_candidates - 3:
            raise ValueError("group_size does not fit the leaf budget")
        self.inner = inner
        self.max_leaf_candidates = max_leaf_candidates
        self.group_size = group_size

    async def choose(
        self,
        *,
        goal: str,
        observation: Observation,
        candidates: list[Candidate],
        history: list[StepRecord],
    ) -> Decision:
        if len(candidates) <= self.max_leaf_candidates:
            return await self.inner.choose(
                goal=goal,
                observation=observation,
                candidates=candidates,
                history=history,
            )

        shortlist = _relevant_shortlist(
            goal,
            candidates,
            limit=self.max_leaf_candidates,
        )
        if shortlist is not None:
            return await self.inner.choose(
                goal=goal,
                observation=observation,
                candidates=shortlist,
                history=history,
            )

        terminals = [
            candidate
            for candidate in candidates
            if candidate.id in {"done", "reobserve", "abstain"}
        ]
        actions = [
            candidate
            for candidate in candidates
            if candidate.id not in {"done", "reobserve", "abstain"}
        ]
        groups = [
            actions[index : index + self.group_size]
            for index in range(0, len(actions), self.group_size)
        ]
        group_candidates: list[Candidate] = []
        for index, group in enumerate(groups):
            preview = "; ".join(
                f"{candidate.id}: {candidate.description}"
                for candidate in group
            )
            group_candidates.append(
                Candidate(
                    id=f"group-{index}",
                    description=(
                        "Choose this action group if the useful next action is "
                        f"inside it: {preview[:3500]}"
                    ),
                    tool=None,
                    arguments={},
                    source="hierarchy",
                )
            )
        group_candidates.extend(terminals)
        group_decision = await self.inner.choose(
            goal=goal,
            observation=observation,
            candidates=group_candidates,
            history=history,
        )
        group_assessment = assess_decision(
            group_decision,
            group_candidates,
            min_confidence=0.45,
            probability_floor=0.25,
            min_margin=0.08,
        )
        if group_decision.selected_id in {"done", "reobserve", "abstain"}:
            return group_decision
        if not group_assessment.accepted:
            terminal = next(
                (
                    candidate
                    for candidate in terminals
                    if candidate.id == "reobserve"
                ),
                None,
            )
            if terminal is None:
                terminal = next(
                    candidate
                    for candidate in terminals
                    if candidate.id == "abstain"
                )
            return Decision(
                selected_id=terminal.id,
                confidence=group_decision.confidence,
                probabilities=group_decision.probabilities,
                model=group_decision.model,
            )
        if not group_decision.selected_id.startswith("group-"):
            raise ValueError(
                f"hierarchical chooser selected unknown group: {group_decision.selected_id}"
            )
        try:
            group_index = int(group_decision.selected_id.split("-", 1)[1])
            selected_group = groups[group_index]
        except (ValueError, IndexError):
            raise ValueError("hierarchical chooser selected malformed group") from None

        leaf_candidates = list(selected_group) + terminals
        leaf = await self.inner.choose(
            goal=goal,
            observation=observation,
            candidates=leaf_candidates,
            history=history,
        )
        return Decision(
            selected_id=leaf.selected_id,
            # The group choice is a retrieval/narrowing stage. Its ambiguity is
            # handled above. Expose the leaf action confidence here instead of
            # depressing every action with min(group, leaf).
            confidence=leaf.confidence,
            probabilities=leaf.probabilities,
            model=leaf.model or group_decision.model,
        )

    def close(self) -> None:
        close = getattr(self.inner, "close", None)
        if close is not None:
            close()
