from __future__ import annotations

from dataclasses import dataclass

from contracts import ABSTAIN, DONE, REOBSERVE, Candidate, Decision

_TERMINAL_IDS = {DONE, REOBSERVE, ABSTAIN}


@dataclass(frozen=True)
class DecisionAssessment:
    accepted: bool
    selected_probability: float
    runner_up_probability: float
    margin: float
    reason: str


def assess_decision(
    decision: Decision,
    candidates: list[Candidate],
    *,
    min_confidence: float,
    probability_floor: float = 0.45,
    min_margin: float = 0.15,
) -> DecisionAssessment:
    """Assess a Jev choice without treating one absolute score as universal.

    A fixed >0.5 threshold is too strict for bounded categorical choices with
    many alternatives and especially for hierarchical retrieval. Terminal
    choices never mutate the desktop, so they are safe to inspect/verify even
    at low confidence. Mutating choices are accepted either when the provider's
    confidence clears the caller threshold or when the returned categorical
    distribution has a clear winner above a conservative floor.
    """
    ids = {candidate.id for candidate in candidates}
    if decision.selected_id not in ids:
        return DecisionAssessment(False, 0.0, 0.0, 0.0, "unknown candidate")

    selected_probability = float(
        decision.probabilities.get(
            decision.selected_id,
            decision.confidence,
        )
    )
    runner_up = max(
        (
            float(probability)
            for candidate_id, probability in decision.probabilities.items()
            if candidate_id != decision.selected_id and candidate_id in ids
        ),
        default=0.0,
    )
    margin = selected_probability - runner_up

    if decision.selected_id in _TERMINAL_IDS:
        return DecisionAssessment(
            True,
            selected_probability,
            runner_up,
            margin,
            "non-mutating terminal choice",
        )

    if decision.confidence >= min_confidence:
        return DecisionAssessment(
            True,
            selected_probability,
            runner_up,
            margin,
            "absolute confidence threshold",
        )

    if (
        selected_probability >= probability_floor
        and margin >= min_margin
    ):
        return DecisionAssessment(
            True,
            selected_probability,
            runner_up,
            margin,
            "clear categorical winner",
        )

    return DecisionAssessment(
        False,
        selected_probability,
        runner_up,
        margin,
        "ambiguous categorical choice",
    )
