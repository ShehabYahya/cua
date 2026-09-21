from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Mapping

from candidates import build_candidates, shortlist_candidates
from contracts import (
    ABSTAIN,
    DONE,
    REOBSERVE,
    Candidate,
    Chooser,
    ConfirmationCallback,
    DesktopDriver,
    DriverRefusal,
    Observation,
    Plan,
    PlanStep,
    StepRecord,
)
from perception import NoopPerceiver
from planner import PassThroughPlanner
from policy import classify_risk, sensitive_text_intent
from resources import DownloadTracker
from writer import (
    PreparedText,
    inferred_text_slots,
    quoted_text_slots,
)

"""One Jev decision per iteration, executed against Cua Driver.

Every iteration refreshes the window inventory once, observes exactly one target
window, builds the complete local action pool, shortens it to a bounded Jev
choice set, asks Jev once, executes what it selected, and then reads the
resulting state — which *is* the next iteration's observation. There is no
planner, no independent verifier, no confidence gate, and no post-action
observation followed by an immediate pre-action observation.
"""

SESSION_SOURCE = "session"
MORE_ACTIONS = "more-actions"
DISCOVER_APPS = "discover-apps"
PREPARE_TEXT = "prepare-text"
INSPECT_SCREEN = "inspect-screen"

# Sentinel status returned by _observe when the Driver lifecycle session ended.
_REVIVED = "__revived__"


def _pair(window: Mapping[str, object] | None) -> tuple[int, int] | None:
    if not isinstance(window, Mapping):
        return None
    pid = window.get("pid")
    window_id = window.get("window_id")
    if not isinstance(pid, int) or not isinstance(window_id, int):
        return None
    return pid, window_id


def _label(window: Mapping[str, object]) -> str:
    app = str(window.get("app_name") or window.get("name") or "window")
    title = str(window.get("title") or "").strip()
    return f"{app} — {title}" if title else app


@dataclass(frozen=True)
class RunResult:
    status: str
    steps: tuple[StepRecord, ...]
    message: str
    plan: Plan
    completed_subgoals: int = 0


@dataclass(frozen=True)
class _Gate:
    status: str
    message: str
    outcome: str = "policy_denied"


@dataclass(frozen=True)
class _BringResult:
    activated: bool
    outcome: str
    reason: str | None = None


class AgentLoop:
    def __init__(
        self,
        driver: DesktopDriver,
        chooser: Chooser,
        *,
        writer=None,
        perceiver=None,
        max_steps: int = 30,
        max_candidates: int = 32,
        download_root: str | None = None,
        progress: Callable[[str], None] | None = None,
        enforce_policy: bool = False,
        visual_click_mode: str = "strict",
    ) -> None:
        self._driver = driver
        self._chooser = chooser
        self._writer = writer
        self._perceiver = perceiver if perceiver is not None else NoopPerceiver()
        self._max_steps = max(1, int(max_steps))
        self._max_candidates = max(4, int(max_candidates))
        self._download_root = download_root
        self._download_tracker = DownloadTracker(download_root)
        self._progress_callback = progress
        self._enforce_policy = enforce_policy
        if visual_click_mode not in {"strict", "permissive"}:
            raise ValueError(
                "visual_click_mode must be 'strict' or 'permissive'"
            )
        self._visual_click_mode = visual_click_mode
        self._recent_files: tuple[str, ...] = ()
        self._recent_context: list[str] = []
        self._last_target: tuple[int, int] | None = None
        self._planner = PassThroughPlanner()

    # -- diagnostics -------------------------------------------------------
    def _progress(self, message: str) -> None:
        if self._progress_callback is None:
            return
        try:
            self._progress_callback(message)
        except Exception:
            pass

    @property
    def recent_context(self) -> tuple[str, ...]:
        return tuple(self._recent_context[-8:])

    @property
    def last_target(self) -> tuple[int, int] | None:
        return self._last_target

    def _append_context(self, line: str) -> None:
        self._recent_context.append(line)
        self._recent_context[:] = self._recent_context[-8:]

    def _remember(self, goal: str, result: RunResult) -> None:
        actions = [item.description for item in result.steps if item.executed][-4:]
        self._append_context(
            f"Previous command {goal!r} ended as {result.status}. "
            f"Recent actions: {actions}. Result: {result.message}"
        )

    def _has_composer(self) -> bool:
        return callable(getattr(self._writer, "compose", None))

    def _has_vision(self) -> bool:
        # NoopPerceiver means no grounding capability is configured at all.
        return self._perceiver is not None and not isinstance(
            self._perceiver, NoopPerceiver
        )

    # -- inventory / observation -------------------------------------------
    async def _list_inventory(self) -> tuple[Mapping[str, object], ...] | str:
        """Read the window inventory.

        A real read error propagates truthfully instead of being reported as an
        empty desktop; only `session_ended` is signalled back to the caller so
        the shared revival budget can handle it.
        """
        try:
            return tuple(await self._driver.list_windows())
        except DriverRefusal as refusal:
            if refusal.code == "session_ended":
                return _REVIVED
            raise

    @staticmethod
    def _matching_windows(
        inventory: tuple[Mapping[str, object], ...],
        app: str,
    ) -> list[Mapping[str, object]]:
        needle = app.casefold().strip()
        return [
            window
            for window in inventory
            if needle
            in f"{window.get('app_name', '')} {window.get('title', '')}".casefold()
        ]

    def _pick_target(
        self,
        inventory: tuple[Mapping[str, object], ...],
        app: str | None,
    ) -> tuple[int, int] | None:
        """Resolve a target. A named app with no window resolves to nothing."""
        if not inventory:
            return None
        if app:
            matched = self._matching_windows(inventory, app)
            if not matched:
                # Never silently fall back to another application when the
                # caller explicitly named one.
                return None
            return self._best_window(matched)
        return self._best_window(list(inventory))

    def _select_target(
        self,
        inventory: tuple[Mapping[str, object], ...],
        *,
        target: tuple[int, int] | None,
        wanted_app: str | None,
        pending: bool,
        launch_name: str | None = None,
    ) -> tuple[int, int] | None:
        """Resolve the target for a fresh inventory.

        `pending=True` means a run is already under way: keep the current target
        if it is still present, otherwise expose desktop selection. Only
        `pending=False` (initial selection) may prefer a default window.
        """
        if launch_name:
            matched = self._matching_windows(inventory, launch_name)
            return self._best_window(matched) if matched else None
        if pending:
            if target is not None and target in {_pair(w) for w in inventory}:
                return target
            return None
        if wanted_app:
            return self._pick_target(inventory, wanted_app)
        if self._last_target is not None and self._last_target in {
            _pair(window) for window in inventory
        }:
            return self._last_target
        return self._pick_target(inventory, None)

    @staticmethod
    def _best_window(
        windows: list[Mapping[str, object]],
    ) -> tuple[int, int] | None:
        visible = [w for w in windows if w.get("is_on_screen", True)] or windows
        focused = [w for w in visible if w.get("is_focused") is True]
        if focused:
            return _pair(focused[0])
        with_z = [w for w in visible if isinstance(w.get("z_index"), int)]
        if with_z:
            return _pair(max(with_z, key=lambda w: int(w["z_index"])))

        def area(window: Mapping[str, object]) -> float:
            bounds = window.get("bounds")
            if not isinstance(bounds, Mapping):
                return 0.0
            return float(bounds.get("width", 0) or 0) * float(
                bounds.get("height", 0) or 0
            )

        return _pair(max(visible, key=area))

    def _desktop_only(
        self,
        inventory: tuple[Mapping[str, object], ...],
    ) -> Observation:
        return Observation(
            snapshot_id="desktop",
            pid=0,
            window_id=0,
            app="desktop",
            window_title="",
            elements=(),
            desktop_windows=inventory,
        )

    async def _observe(
        self,
        inventory: tuple[Mapping[str, object], ...],
        target: tuple[int, int] | None,
        *,
        app: str | None = None,
        include_screenshot: bool = False,
        enrich_goal: str | None = None,
    ) -> Observation | str:
        if target is None:
            # Exact inventory says the target is absent: desktop selection is
            # the correct state, not a redirected window.
            return self._desktop_only(inventory)
        try:
            observation = await self._driver.observe(
                app,
                include_screenshot=include_screenshot,
                windows=inventory,
                target_window=target,
            )
        except DriverRefusal as refusal:
            if refusal.code == "session_ended":
                # Let the caller revive once and re-observe.
                return _REVIVED
            # Any other refusal is a real failure and must not be reported as a
            # successful read of a desktop-only state.
            raise
        if include_screenshot and enrich_goal is not None:
            observation = await self._enrich(enrich_goal, observation)
        return observation

    async def _enrich(self, goal: str, observation: Observation) -> Observation:
        try:
            return await self._perceiver.enrich(goal, observation)
        except Exception as error:
            self._progress(f"Visual grounding failed: {error}")
            return observation

    async def _read_state(
        self,
        *,
        target: tuple[int, int] | None,
        wanted_app: str | None,
        pending: bool,
        launch_name: str | None = None,
    ) -> tuple[Observation, tuple[Mapping[str, object], ...], tuple[int, int] | None] | str:
        """One shared read path: inventory, target selection, observation.

        Returns (observation, inventory, target), or `_REVIVED` when the Driver
        lifecycle session ended. Real read errors propagate; a target absent from
        the exact inventory becomes desktop selection rather than a substituted
        window.
        """
        inventory = await self._list_inventory()
        if inventory == _REVIVED:
            return _REVIVED
        if launch_name:
            matched = self._matching_windows(inventory, launch_name)
            if not matched:
                # The launched window is not here; remain in desktop selection
                # and never launch the same app again automatically.
                return self._desktop_only(inventory), inventory, None
        target = self._select_target(
            inventory,
            target=target,
            wanted_app=wanted_app,
            pending=pending,
            launch_name=launch_name,
        )
        observation = await self._observe(inventory, target, app=wanted_app)
        if observation == _REVIVED:
            return _REVIVED
        if observation.desktop_windows:
            inventory = tuple(observation.desktop_windows)
        if observation.pid:
            target = (observation.pid, observation.window_id)
            self._last_target = target
        return observation, inventory, target

    # -- candidate pool ----------------------------------------------------
    def _pool_limit(self, observation: Observation, slots: tuple[PreparedText, ...]) -> int:
        # Slot count is part of the budget: every supplied local slot must stay
        # reachable even after several were composed on demand. The slot term
        # upper-bounds two grounded type candidates per element per slot
        # (semantic + browser), one focused route per slot, and three bundles
        # per slot (fill-submit, browser-search, new-tab-search), as agreed with
        # the candidates.py owner.
        return max(
            96,
            24 * len(observation.elements)
            + 2 * len(observation.visual_regions)
            + 2 * len(slots) * (len(observation.elements) + 2)
            + 128,
        )

    def _build_pool(
        self,
        goal: str,
        observation: Observation,
        slots: tuple[PreparedText, ...],
    ) -> list[Candidate]:
        if not observation.pid:
            # Desktop/session-selection state: no window-targeted shortcuts,
            # bundles, clicks or typing can be valid. Terminals are still
            # required so an empty desktop can still be reported complete,
            # refreshed, or abandoned instead of producing an empty Choice.
            return [
                Candidate(
                    DONE,
                    "The current subgoal is already complete; stop acting on it.",
                    None,
                    {},
                    source="terminal",
                ),
                Candidate(
                    REOBSERVE,
                    "Discard this decision set and obtain a fresh observation.",
                    None,
                    {},
                    source="terminal",
                ),
                Candidate(
                    ABSTAIN,
                    "Stop without acting because none of the proposed actions is "
                    "safe or useful.",
                    None,
                    {},
                    source="terminal",
                ),
            ]
        return build_candidates(
            goal,
            observation,
            prepared_texts=slots,
            max_candidates=self._pool_limit(observation, slots),
            allow_visual_clicks=bool(
                self._driver.capture_bound_click
                or (
                    self._visual_click_mode == "permissive"
                    and getattr(
                        self._driver,
                        "coordinate_click_supported",
                        False,
                    )
                )
            ),
            download_root=self._download_root,
            recent_files=self._download_tracker.validate_recent(self._recent_files),
            enforce_policy=self._enforce_policy,
        )

    @staticmethod
    def _more_actions() -> Candidate:
        return Candidate(
            id=MORE_ACTIONS,
            description=(
                "Show more of the remaining controls in this window; none of the "
                "offered choices is the right next step."
            ),
            tool=None,
            arguments={},
            source=SESSION_SOURCE,
        )

    def _session_candidates(
        self,
        *,
        inventory: tuple[Mapping[str, object], ...],
        target: tuple[int, int] | None,
        apps: tuple[Mapping[str, object], ...],
        suppressed: set[str],
    ) -> list[Candidate]:
        out: list[Candidate] = []
        for window in inventory:
            pair = _pair(window)
            if pair is None or pair == target:
                continue
            candidate = Candidate(
                id=f"switch-window-{pair[0]}-{pair[1]}",
                description=f"Switch to the window {_label(window)!r}.",
                tool=None,
                arguments={"pid": pair[0], "window_id": pair[1]},
                source=SESSION_SOURCE,
            )
            if candidate.id not in suppressed:
                out.append(candidate)
        if apps:
            for index, app in enumerate(apps, start=1):
                name = app.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                candidate = Candidate(
                    id=f"launch-app-{index}",
                    description=f"Launch the application {name.strip()!r}.",
                    tool=None,
                    arguments={"name": name.strip()},
                    source=SESSION_SOURCE,
                )
                if candidate.id not in suppressed:
                    out.append(candidate)
        elif DISCOVER_APPS not in suppressed:
            out.append(
                Candidate(
                    id=DISCOVER_APPS,
                    description=(
                        "Ask the Driver which applications are installed, so one "
                        "can be launched."
                    ),
                    tool=None,
                    arguments={},
                    source=SESSION_SOURCE,
                )
            )
        if (
            target is not None
            and self._has_vision()
            and INSPECT_SCREEN not in suppressed
        ):
            out.append(
                Candidate(
                    id=INSPECT_SCREEN,
                    description=(
                        "Capture this window's screenshot and ground its controls "
                        "visually, then choose again."
                    ),
                    tool=None,
                    arguments={},
                    source=SESSION_SOURCE,
                )
            )
        # Text can be composed even when nothing local was extracted yet.
        if self._has_composer() and PREPARE_TEXT not in suppressed:
            out.append(
                Candidate(
                    id=PREPARE_TEXT,
                    description=(
                        "Ask the companion model for the ordinary text this goal "
                        "needs, then choose again."
                    ),
                    tool=None,
                    arguments={},
                    source=SESSION_SOURCE,
                )
            )
        return out

    # -- execution ---------------------------------------------------------
    async def _execute(
        self,
        candidate: Candidate,
        *,
        act: bool,
        allow_foreground: bool,
        cancel_event,
        result_ref: dict,
    ) -> tuple[str, str, int]:
        """Returns (outcome, reason, delivered_prefix_count)."""
        primitives = tuple(candidate.steps) or (candidate,)
        for primitive in primitives:
            if primitive.tool is None:
                raise ValueError("candidate has no executable tool")
        if not act:
            return "dry_run", candidate.description, 0

        delivered = 0
        for index, primitive in enumerate(primitives):
            if cancel_event is not None and cancel_event.is_set():
                return (
                    "cancelled_after_action" if delivered else "cancelled",
                    f"cancelled before primitive {index + 1}/{len(primitives)}",
                    delivered,
                )
            try:
                result_ref["result"] = await self._driver.execute(primitive)
                delivered += 1
                continue
            except DriverRefusal as refusal:
                if (
                    refusal.code in {"session_ended", "tool_invocation_failed"}
                    and delivered == 0
                ):
                    return "session_revived", refusal.reason, 0
                if refusal.recommended == "foreground" and allow_foreground:
                    if cancel_event is not None and cancel_event.is_set():
                        return "cancelled_after_action", "cancelled before foreground retry", delivered
                    try:
                        result_ref["result"] = await self._driver.execute(
                            self._driver.with_foreground(primitive)
                        )
                        delivered += 1
                        continue
                    except DriverRefusal as foreground_refusal:
                        if (
                            foreground_refusal.code
                            in {"session_ended", "tool_invocation_failed"}
                            and delivered == 0
                        ):
                            return "session_revived", foreground_refusal.reason, 0
                        outcome, reason = self._refusal_outcome(foreground_refusal)
                        if delivered:
                            outcome = "partial"
                            reason = (
                                f"{delivered}/{len(primitives)} primitive(s) were "
                                f"delivered; stopped at primitive {index + 1} "
                                f"(foreground retry): {foreground_refusal}"
                            )
                        return outcome, reason, delivered
                    except Exception as error:
                        return (
                            self._prefix_outcome(delivered, index, len(primitives), error),
                            self._prefix_reason(delivered, index, len(primitives), error),
                            delivered,
                        )
                return self._exit_for(refusal, delivered, index, len(primitives))
            except Exception as error:
                # A generic failed call is not proof of delivery; only primitives
                # that returned successfully are counted as delivered.
                return (
                    self._prefix_outcome(delivered, index, len(primitives), error),
                    self._prefix_reason(delivered, index, len(primitives), error),
                    delivered,
                )
        return "executed", "", delivered

    @staticmethod
    def _prefix_outcome(
        delivered: int, index: int, total: int, error: object
    ) -> str:
        return "partial" if delivered else "failed"

    @staticmethod
    def _prefix_reason(
        delivered: int, index: int, total: int, error: object
    ) -> str:
        if delivered:
            return (
                f"{delivered}/{total} primitive(s) were delivered; stopped at "
                f"primitive {index + 1}: {error} (delivery of the failing "
                "primitive is unknown)"
            )
        return f"primitive {index + 1}/{total} failed before delivery: {error}"

    def _exit_for(
        self,
        refusal: DriverRefusal,
        delivered: int,
        index: int,
        total: int,
    ) -> tuple[str, str, int]:
        outcome, reason = self._refusal_outcome(refusal)
        if delivered:
            return (
                "partial",
                f"{delivered}/{total} primitive(s) were delivered; stopped at "
                f"primitive {index + 1}: {refusal}",
                delivered,
            )
        return outcome, reason, delivered

    @staticmethod
    def _refusal_outcome(refusal: DriverRefusal) -> tuple[str, str]:
        if refusal.recommended == "foreground":
            return "needs_foreground", str(refusal)
        return "refused", str(refusal)

    @staticmethod
    def _effect(result: object) -> str | None:
        if not isinstance(result, Mapping):
            return None
        effect = result.get("effect")
        return str(effect) if effect is not None else None

    @staticmethod
    def _is_download(candidate: Candidate) -> bool:
        if candidate.tool == "browser_download":
            return True
        return any(step.tool == "browser_download" for step in candidate.steps)

    # -- session operations -------------------------------------------------
    async def _discover_apps(self) -> tuple[Mapping[str, object], ...]:
        try:
            return tuple(await self._driver.list_apps())
        except Exception as error:
            self._progress(f"App discovery failed: {error}")
            return ()

    async def _prepare_text(
        self,
        goal: str,
        observation: Observation,
        history: list[StepRecord],
        slots: tuple[PreparedText, ...],
    ) -> tuple[tuple[PreparedText, ...], bool]:
        """Returns (slots, produced_something_new)."""
        compose = getattr(self._writer, "compose", None)
        if not callable(compose):
            self._progress("No text composer is configured; keeping current slots.")
            return slots, False
        try:
            added = await compose(
                original_goal=goal,
                observation=observation,
                history=history,
                existing=slots,
            )
        except Exception as error:
            self._progress(f"Text preparation failed: {error}")
            return slots, False
        known = {slot.text for slot in slots}
        merged = list(slots)
        for slot in tuple(added or ()):
            if slot.text and slot.text not in known:
                known.add(slot.text)
                merged.append(slot)
        return tuple(merged), len(merged) > len(slots)

    async def _bring_forward(self, candidate: Candidate) -> _BringResult:
        pid = int(candidate.arguments["pid"])
        window_id = int(candidate.arguments["window_id"])
        has_tool = getattr(self._driver, "has_tool", None)
        if not callable(has_tool) or not has_tool("bring_to_front"):
            self._progress(
                "The Driver does not advertise bring_to_front; the target is "
                "selected but not physically activated."
            )
            return _BringResult(False, "target_selected")
        activation = Candidate(
            id="session-bring-to-front",
            description="Bring the selected window to the front.",
            tool="bring_to_front",
            arguments={"pid": pid, "window_id": window_id},
        )
        try:
            await self._driver.execute(activation)
        except DriverRefusal as refusal:
            self._progress(f"bring_to_front refused: {refusal}")
            return _BringResult(False, "activation_refused", str(refusal))
        except Exception as error:
            self._progress(f"bring_to_front failed: {error}")
            return _BringResult(False, "activation_failed", str(error))
        return _BringResult(True, "activated")

    async def _policy_gate(
        self,
        candidate: Candidate,
        goal: str,
        approve_consequential: bool,
        confirm: ConfirmationCallback | None,
    ) -> tuple[_Gate | None, bool]:
        if candidate.risk == "safe":
            risk = classify_risk(candidate.description, tool=candidate.tool)
            if risk != candidate.risk:
                candidate = replace(candidate, risk=risk)
        if candidate.risk == "deny" or (
            candidate.tool in {"type_text", "browser_type"}
            and sensitive_text_intent(goal)
        ):
            return _Gate("blocked", f"Policy denied: {candidate.description}"), False
        if candidate.risk == "confirm" and not approve_consequential:
            approved = await confirm(candidate) if confirm is not None else False
            return None, approved
        return None, True

    # -- main loop ---------------------------------------------------------
    async def run(
        self,
        goal: str,
        *,
        app: str | None = None,
        act: bool = False,
        approve_consequential: bool = False,
        allow_foreground: bool = False,
        confirm: ConfirmationCallback | None = None,
        cancel_event=None,
    ) -> RunResult:
        plan = Plan(goal=goal, steps=(PlanStep(goal=goal, app=app, completion=goal),))

        def stop(
            status: str,
            message: str,
            steps: list[StepRecord],
            completed: int = 0,
        ) -> RunResult:
            result = RunResult(status, tuple(steps), message, plan, completed)
            self._remember(goal, result)
            return result

        def cancelled() -> bool:
            return cancel_event is not None and cancel_event.is_set()

        if self._enforce_policy and sensitive_text_intent(goal):
            return stop(
                "blocked",
                "Credential-like text entry is blocked before choosing an action.",
                [],
            )
        if cancelled():
            return stop("cancelled", "Cancelled before execution.", [])

        # One revival budget for the whole run, shared by startup, fresh reads
        # and post-action reads.
        revivals = 0
        self._progress("Reading desktop state...")
        try:
            overview = await self._driver.desktop_overview(
                include_screenshot=False,
                include_apps=False,
            )
        except DriverRefusal as refusal:
            if refusal.code != "session_ended":
                raise
            # A revival is allowed once; a second startup refusal propagates.
            revivals += 1
            self._progress("Reviving the Driver lifecycle session...")
            await self._driver.revive_session()
            overview = await self._driver.desktop_overview(
                include_screenshot=False,
                include_apps=False,
            )
        inventory: tuple[Mapping[str, object], ...] = tuple(overview.windows)
        wanted_app = app or self._planner._infer_app(goal, overview)
        target = self._select_target(
            inventory, target=None, wanted_app=wanted_app, pending=False
        )
        current: Observation = self._desktop_only(inventory)
        for _ in range(2):
            observed = await self._observe(inventory, target, app=wanted_app)
            if observed == _REVIVED:
                if revivals >= 1:
                    return stop(
                        "refused",
                        "The Driver lifecycle session ended twice while starting.",
                        [],
                    )
                revivals += 1
                self._progress("Reviving the Driver lifecycle session...")
                await self._driver.revive_session()
                continue
            current = observed
            break
        if current.desktop_windows:
            inventory = tuple(current.desktop_windows)
        if current.pid:
            self._last_target = (current.pid, current.window_id)
        self._progress(
            f"Desktop ready: {len(inventory)} window(s); targeting {current.app!r}."
        )

        apps: tuple[Mapping[str, object], ...] = ()
        apps_loaded = False
        launch_name: str | None = None
        slots = self._local_slots(goal)
        page = 0
        history: list[StepRecord] = []
        failed_routes: dict[str, str] = {}
        inspected: set[tuple[tuple[int, int], str]] = set()
        auto_inspected: set[tuple[tuple[int, int], str]] = set()
        fresh = False
        refreshes = 0
        step = 0

        while step < self._max_steps:
            if cancelled():
                return stop("cancelled", "Cancelled by the user.", history)

            if fresh:
                prior_fingerprint = current.fingerprint()
                read = await self._read_state(
                    target=target,
                    wanted_app=wanted_app,
                    pending=True,
                    launch_name=launch_name,
                )
                if read == _REVIVED:
                    if revivals >= 1:
                        return stop(
                            "refused",
                            "The Driver lifecycle session ended twice in this run.",
                            history,
                        )
                    revivals += 1
                    self._progress("Reviving the Driver lifecycle session...")
                    await self._driver.revive_session()
                    # Keep launch_name until the read actually succeeds.
                    continue
                current, inventory, target = read
                launch_name = None
                if current.fingerprint() != prior_fingerprint:
                    # A real state change invalidates paging and refresh pressure.
                    page = 0
                    refreshes = 0
                fresh = False

            # An explicitly requested app with no window triggers one app
            # inventory read so its matches can be offered; no silent redirect.
            if wanted_app and target is None and not apps_loaded and not cancelled():
                apps = await self._discover_apps()
                apps_loaded = True
                wanted = wanted_app.casefold().strip()
                apps = tuple(
                    app
                    for app in apps
                    if wanted in str(app.get("name") or "").casefold()
                )

            context = current.fingerprint()
            suppressed = {
                candidate_id
                for candidate_id, snapshot in failed_routes.items()
                if snapshot == context
            }
            pool = self._build_pool(goal, current, slots)
            # Paging is a session operation, not a window operation: keep it
            # available so large inventories and app lists stay reachable.
            pool.append(self._more_actions())
            pool.extend(
                self._session_candidates(
                    inventory=inventory,
                    target=target,
                    apps=apps,
                    suppressed=suppressed,
                )
            )

            # One-time lazy visual fallback: only when there is a real window,
            # grounding capability exists, and no actionable semantic candidate
            # is available. A keyboard shortcut is not grounding for an
            # inaccessible canvas.
            if (
                target is not None
                and self._has_vision()
                and (target, context) not in auto_inspected
                and not self._has_actionable(pool)
            ):
                auto_inspected.add((target, context))
                self._progress("No grounded control found; grounding visually...")
                inspected.add((target, context))
                inspected_state, inspected_inventory = await self._inspect(
                    goal, target, inventory
                )
                if inspected_state is None:
                    # The lifecycle session ended during the capture; let the
                    # shared budget handle it on the next iteration.
                    if revivals >= 1:
                        return stop(
                            "refused",
                            "The Driver lifecycle session ended twice in this run.",
                            history,
                        )
                    revivals += 1
                    self._progress("Reviving the Driver lifecycle session...")
                    await self._driver.revive_session()
                    fresh = True
                    continue
                current = inspected_state
                inventory = inspected_inventory
                context = current.fingerprint()
                suppressed = {
                    candidate_id
                    for candidate_id, snapshot in failed_routes.items()
                    if snapshot == context
                }
                pool = self._build_pool(goal, current, slots)
                pool.append(self._more_actions())
                pool.extend(
                    self._session_candidates(
                        inventory=inventory,
                        target=target,
                        apps=apps,
                        suppressed=suppressed,
                    )
                )

            pool = [
                candidate for candidate in pool if candidate.id not in suppressed
            ]
            observation = self._annotate(current, inventory, self.recent_context)
            shortlist = shortlist_candidates(
                goal,
                pool,
                limit=min(self._max_candidates, 32),
                page=page,
            )
            self._progress(
                f"Asking Jev with {len(shortlist)} of {len(pool)} candidate(s)."
            )
            decision = await self._chooser.choose(
                # The original user instruction, unredacted: Jev must be able to
                # compare the requested query/text with what it observes.
                # Executable tool arguments and generated slot payloads stay
                # local; only slot ids and purposes appear in the candidates.
                goal=goal,
                observation=observation,
                candidates=shortlist,
                history=history,
            )
            if cancelled():
                return stop("cancelled", "Cancelled by the user.", history)
            selected = self._resolve(decision.selected_id, shortlist, observation)
            if selected is None:
                return stop(
                    "refused",
                    "Jev selected a candidate that is not in the current set: "
                    f"{decision.selected_id}",
                    history,
                )
            step += 1
            self._progress(f"Jev selected {selected.id}: {selected.description}")

            def record(
                outcome: str,
                *,
                effect: str | None = None,
                executed: bool = False,
                reason: str | None = None,
            ) -> StepRecord:
                return StepRecord(
                    step,
                    1,
                    observation.snapshot_id,
                    selected.id,
                    selected.description,
                    float(getattr(decision, "confidence", 0.0) or 0.0),
                    executed,
                    outcome,
                    effect=effect,
                    reason=reason,
                )

            # ---- session operations: no UI mutation, observation retained ----
            if selected.id == MORE_ACTIONS:
                page += 1
                history.append(record("paged"))
                continue
            if selected.id == DISCOVER_APPS:
                apps = await self._discover_apps()
                apps_loaded = True
                if apps:
                    page = 0
                    history.append(record("apps_discovered"))
                else:
                    failed_routes[selected.id] = context
                    history.append(record("apps_unavailable"))
                continue
            if selected.id == PREPARE_TEXT:
                if cancelled():
                    return stop("cancelled", "Cancelled by the user.", history)
                # The composer must see the annotated observation (bounded prior
                # command/action context and the fresh window inventory), not the
                # raw unannotated snapshot.
                slots, produced = await self._prepare_text(
                    goal, observation, history, slots
                )
                if produced:
                    # The pool just expanded; start its paging from the top.
                    page = 0
                    history.append(record("text_prepared"))
                else:
                    failed_routes[selected.id] = context
                    history.append(
                        record(
                            "text_unavailable",
                            reason="no new text was produced for this state",
                        )
                    )
                continue
            if selected.id == INSPECT_SCREEN:
                if target is None or (target, context) in inspected:
                    failed_routes[selected.id] = context
                    history.append(record("inspect_unavailable"))
                    continue
                inspected.add((target, context))
                inspected_state, read_inventory = await self._inspect(
                    goal, target, inventory
                )
                if inspected_state is None:
                    # The lifecycle session ended during the capture. Keep the
                    # previous observation and re-read on the next iteration.
                    if revivals >= 1:
                        return stop(
                            "refused",
                            "The Driver lifecycle session ended twice in this run.",
                            history,
                        )
                    revivals += 1
                    self._progress("Reviving the Driver lifecycle session...")
                    await self._driver.revive_session()
                    fresh = True
                    continue
                current = inspected_state
                inventory = read_inventory
                if current.visual_regions:
                    page = 0
                    history.append(record("inspected"))
                else:
                    failed_routes[selected.id] = current.fingerprint()
                    history.append(record("inspect_no_regions"))
                continue

            # ---- mutating target changes: gated by dry-run and cancellation ---
            if selected.id.startswith("switch-window-"):
                if not act:
                    history.append(record("dry_run"))
                    return stop("dry_run", selected.description, history)
                if cancelled():
                    return stop("cancelled", "Cancelled by the user.", history)
                brought = await self._bring_forward(selected)
                target = (
                    int(selected.arguments["pid"]),
                    int(selected.arguments["window_id"]),
                )
                self._last_target = target
                if brought.activated:
                    history.append(record("switched", reason=brought.reason))
                else:
                    history.append(
                        record(
                            "target_selected",
                            reason=(
                                f"{brought.outcome}: {brought.reason}"
                                if brought.reason
                                else brought.outcome
                            ),
                        )
                    )
                fresh = True
                continue
            if selected.id.startswith("launch-app-"):
                if not act:
                    history.append(record("dry_run"))
                    return stop("dry_run", selected.description, history)
                if cancelled():
                    return stop("cancelled", "Cancelled by the user.", history)
                name = str(selected.arguments.get("name") or "")
                try:
                    await self._driver.ensure_app(name)
                    outcome, reason = "launched", None
                    # Resolve only this launched app's window on the next fresh
                    # inventory; never launch automatically again.
                    launch_name = name
                except Exception as error:
                    outcome, reason = "launch_failed", str(error)
                    failed_routes[selected.id] = context
                history.append(record(outcome, reason=reason))
                fresh = True
                continue

            # ---- terminal and concrete actions -------------------------------
            if selected.id == REOBSERVE:
                refreshes += 1
                history.append(record("reobserve"))
                if refreshes > 2:
                    failed_routes[selected.id] = context
                    self._progress(
                        "Repeated refresh requests on an unchanged state are "
                        "suppressed so Jev must act, abstain, or report done."
                    )
                fresh = True
                continue
            if selected.id == ABSTAIN:
                history.append(record("abstained"))
                return stop("abstained", selected.description, history)
            if selected.id == DONE:
                history.append(record("done"))
                return stop(
                    "completed",
                    "Jev reports the goal complete",
                    history,
                    completed=1,
                )

            if self._enforce_policy:
                denial, approved = await self._policy_gate(
                    selected, goal, approve_consequential, confirm
                )
                if denial is not None:
                    history.append(record(denial.outcome))
                    return stop(denial.status, denial.message, history)
                if not approved:
                    history.append(record("needs_confirmation"))
                    return stop("needs_confirmation", selected.description, history)

            if not act:
                history.append(record("dry_run"))
                return stop("dry_run", selected.description, history)

            files_before = (
                self._download_tracker.snapshot() if self._is_download(selected) else {}
            )
            self._progress(f"Executing: {selected.description}")
            result_ref: dict = {}
            outcome, reason, delivered = await self._execute(
                selected,
                act=act,
                allow_foreground=allow_foreground,
                cancel_event=cancel_event,
                result_ref=result_ref,
            )

            if outcome == "session_revived":
                if revivals >= 1:
                    history.append(record("session_ended", reason=reason))
                    return stop(
                        "refused",
                        "The Driver lifecycle session ended twice in this run.",
                        history,
                    )
                revivals += 1
                self._progress("Reviving the Driver lifecycle session...")
                await self._driver.revive_session()
                history.append(record("session_revived", reason=reason))
                fresh = True
                continue
            if outcome in {"needs_foreground", "refused", "partial", "failed"}:
                history.append(
                    record(
                        outcome,
                        reason=reason,
                        executed=bool(delivered),
                    )
                )
                return stop(outcome, reason or outcome, history)
            if outcome in {"cancelled", "cancelled_after_action"}:
                history.append(record(outcome, executed=bool(delivered)))
                return stop("cancelled", "Cancelled by the user.", history)

            if files_before or self._is_download(selected):
                changes = await self._download_tracker.wait_for_changes(
                    files_before, timeout=5.0
                )
                if changes:
                    names = ", ".join(f'"{s.name}"' for s in changes[:4])
                    self._recent_files = self._download_tracker.validate_recent(
                        tuple(s.path for s in changes) + self._recent_files
                    )
                    self._append_context(f"Local file resource changed: {names}.")

            # The resulting state is the next iteration's observation, so no
            # separate post-action observation is taken at the bottom.
            read = await self._read_state(
                target=target, wanted_app=wanted_app, pending=True
            )
            if read == _REVIVED:
                history.append(record("session_ended", executed=bool(delivered)))
                if revivals >= 1:
                    return stop(
                        "refused",
                        "The Driver lifecycle session ended twice in this run.",
                        history,
                    )
                revivals += 1
                await self._driver.revive_session()
                fresh = True
                continue
            nxt, inventory, target = read
            changed = current.fingerprint() != nxt.fingerprint()
            if not changed:
                failed_routes[selected.id] = context
                self._progress(
                    "That route produced no observable change; it is suppressed on "
                    "this state so Jev can pick another action."
                )
            else:
                # A real state change invalidates paging and refresh pressure.
                page = 0
                refreshes = 0
            history.append(
                record(
                    "executed" if changed else "executed_no_change",
                    effect=self._effect(result_ref.get("result")),
                    executed=bool(delivered),
                    reason=(
                        "state changed after the action"
                        if changed
                        else "no semantic state change detected"
                    ),
                )
            )
            self._append_context(
                f"In {nxt.app} / {nxt.window_title!r}: {selected.description}. "
                f"Resulting view: {nxt.browser_title or nxt.window_title!r}."
            )
            current = nxt
            # This observation was taken after the action; reuse it and the
            # single fresh inventory rather than listing again next iteration.
            fresh = False

        return stop(
            "budget_exhausted",
            "The step budget was exhausted before Jev reported completion.",
            history,
        )

    # -- selection helpers --------------------------------------------------
    @staticmethod
    def _has_actionable(pool: list[Candidate]) -> bool:
        """True when a candidate can act on the window's actual controls."""
        for candidate in pool:
            if candidate.source == SESSION_SOURCE or candidate.source == "terminal":
                continue
            if candidate.id in {DONE, REOBSERVE, ABSTAIN, MORE_ACTIONS}:
                continue
            if candidate.steps:
                return True
            if candidate.tool in {
                "click",
                "double_click",
                "right_click",
                "type_text",
                "browser_click",
                "browser_type",
                "browser_pointer",
                "browser_navigate",
                "browser_download",
                "browser_set_input_files",
            }:
                return True
        return False

    @staticmethod
    def _annotate(
        observation: Observation,
        inventory: tuple[Mapping[str, object], ...],
        recent_context: tuple[str, ...],
    ) -> Observation:
        """Attach the bounded prior-command context and the fresh inventory.

        Used both for the Jev decision and for the text composer, so the composer
        receives `recent_context` rather than a raw snapshot.
        """
        return replace(
            observation,
            recent_context=recent_context,
            desktop_windows=inventory,
        )

    def _local_slots(self, goal: str) -> tuple[PreparedText, ...]:
        slots = list(quoted_text_slots(goal))
        for slot in inferred_text_slots(goal):
            if all(existing.text != slot.text for existing in slots):
                slots.append(slot)
        return tuple(slots)

    def _resolve(
        self,
        selected_id: str,
        candidates: list[Candidate],
        observation: Observation,
    ) -> Candidate | None:
        by_id: dict[str, Candidate] = {}
        for candidate in candidates:
            if candidate.id in by_id:
                raise ValueError("candidate table contains duplicate IDs")
            by_id[candidate.id] = candidate
        candidate = by_id.get(selected_id)
        if candidate is None:
            return None
        if (
            candidate.snapshot_id is not None
            and candidate.snapshot_id != observation.snapshot_id
        ):
            self._progress("Rejecting a candidate bound to a stale snapshot.")
            return None
        return candidate

    async def _inspect(
        self,
        goal: str,
        target: tuple[int, int],
        inventory: tuple[Mapping[str, object], ...],
    ) -> tuple[Observation | None, tuple[Mapping[str, object], ...]]:
        """Re-observe the same window with a screenshot.

        Returns (observation, inventory), or (None, inventory) when the Driver
        lifecycle session ended. Other refusals propagate: a failed capture is
        never reported as a read.
        """
        try:
            captured = await self._driver.observe(
                None,
                include_screenshot=True,
                windows=inventory,
                target_window=target,
            )
        except DriverRefusal as refusal:
            if refusal.code == "session_ended":
                return None, inventory
            # A refused capture is a real failure; never claim a read happened.
            raise
        refreshed = tuple(captured.desktop_windows) or inventory
        return await self._enrich(goal, captured), refreshed
