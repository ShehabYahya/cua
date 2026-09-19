# Jev desktop agent

A general desktop-control harness on top of **Cua Driver** and **TypeSafe Jev**.
The application owns planning, perception, candidate construction, policy,
execution, recovery, and verification. Jev sees only a bounded set of candidate
IDs/descriptions and selects the next action.

This implementation is designed around the boundary accepted in Cua RFC #3931:
Driver owns desktop authority; the harness constructs complete actions; Jev never
invents tool names, targets, coordinates, or arguments.

## End-to-end architecture

```text
voice or text command
        |
        +--> direct single-goal mode (default; no planner model call)
        |       or
        +--> optional OpenRouter planner (--planner)
        |
        v
Cua desktop overview (visible windows first; richer inventory only when needed)
        |
        v
select/launch target app
        |
        v
Cua get_window_state
  | accessibility tree
  | screenshot
  | capture_id (when supported)
        |
        +--> Cua local visual parser when installed
        |       or
        +--> OpenRouter vision fallback
        |
        v
bounded candidate table
(click/type/hotkey/scroll/visual + done/reobserve/abstain)
        |
        v
OpenRouter Decisions API --> TypeSafe Jev chooses ONE supplied ID
        |
        v
local policy + stale-state validation
        |
        v
Cua executes at most one action
        |
        v
fresh observation --> next Jev action
        |
        +--> full verifier only when Jev says done
        +--> stuck detection / optional repair planner / foreground escalation gate
```

## What is implemented

- Persistent Cua Driver MCP session.
- GNOME/Wayland-safe child environment (`IsEnabled` accessibility advertisement;
  no `ScreenReaderEnabled` advertisement that can launch Orca).
- Native Wayland opt-in when a Wayland session is detected.
- Fast direct mode is the default: the entire user command stays one goal and
  does not make a planner-model call. Visible windows are read first; app
  inventory and desktop screenshots are skipped until needed.
- Optional multi-subgoal OpenRouter planning with `--planner`.
- Automatic app launch for locally inferred or planner-selected applications.
- Runtime preflight diagnostics through `--check`, including Driver health,
  visible-window/app counts, visual/capture capabilities, and GNOME Wayland
  remediation hints.
- Per-window **accessibility + screenshot** observation on every step.
- Correct AT-SPI role handling including `push button`, `page tab`, `entry`,
  menu/list/radio/check controls, and action-advertising widgets.
- Optional Cua `parse_visual_regions` integration.
- OpenRouter vision fallback for inaccessible/custom-drawn controls.
- Capture-bound visual clicks only when Driver advertises `click.capture_id`;
  the harness never downgrades a stale visual click to an unbound coordinate.
- Dynamic semantic actions, prepared-text typing, focused-window typing fallback,
  common hotkeys (including F2 rename), double/right click where requested,
  scrolling, visual actions, `done`, `reobserve`, and `abstain`.
- Opportunistic typed Chromium/Electron page control through exact
  `get_browser_state(..., semantic_v2)` refs. It is used only when Driver proves
  `binding_quality: exact` and `mutation_allowed: true`; unsupported browsers
  such as Firefox continue through native accessibility/visual control. The
  harness never auto-attaches to a personal browser profile or grants browser
  debugging consent.
- Hierarchical Jev action selection: large desktop action pools are grouped and
  narrowed through bounded Jev choices, while each individual Jev request stays
  at 32 candidates or fewer.
- OpenRouter Jev route through `POST /api/alpha/decisions` using
  `~typesafe/jev-latest` by default.
- OpenRouter writer, visual grounding, and postcondition verifier using
  `openrouter/auto` by default; the planner is opt-in. Common search/rename
  text is extracted locally instead of spending a writer-model call.
- One-goal execution by default with fresh state after every mutation; optional
  multi-subgoal execution when `--planner` is explicitly enabled.
- Remote verification is deferred until Jev proposes `done` by default.
  Deterministic local checks verify common states such as typed text, a new tab,
  and search-result pages without a model call. `--verify-every-action`
  restores the slower full-verifier behavior.
- Margin- and risk-aware Jev confidence policy, no-progress detection,
  repeated-action suppression, and bounded recovery.
- Explicit Cua lifecycle-session management and automatic revival after a
  `session_ended` refusal.
- Local consequential-action confirmation policy (send/publish/delete/pay/
  install/permission/account actions) and hard refusal to generate/type password,
  OTP, card-security-code, recovery-code, private-key, or seed-phrase fields.
- Explicit foreground escalation gate: foreground is used only after Driver asks
  for it and only with `--allow-foreground`.
- In-process conversational context for follow-ups during voice sessions.
- Bounded local download-resource memory: only the approved download directory
  (default `~/Downloads`) is watched, recursively to two levels, without reading
  file contents. Newly downloaded/renamed files can be handed back to exact
  `browser_set_input_files` refs without exposing absolute paths to Jev.
- Typed browser downloads to an approved directory and typed browser uploads of
  recently observed local files, both behind the consequential-action gate.
- Voice mode with VAD microphone capture, OpenRouter transcription, spoken
  confirmation of consequential actions, and optional local TTS.

## Prerequisites

Use the newest Cua Driver available on the machine. Older Driver versions can
run the semantic path, but full visual grounding requires the newer capture-ID
and capture-bound-click contracts.

```bash
cua-driver update
cua-driver doctor
```

### GNOME Wayland

The native Wayland path is enabled automatically by this harness. For reliable
GNOME/Mutter window geometry, screenshots, activation, and visual grounding,
install Cua's bundled WinRects helper from this checkout:

```bash
cd ~/cua/libs/cua-driver/wayland-helper
./install.sh
```

Then log out/in once and verify:

```bash
gnome-extensions info winrects@cua
```

It should report `State: ACTIVE`.

## Install

```bash
cd ~/cua/libs/cua-driver/examples/jev-desktop-agent
uv sync
```

For voice mode:

```bash
uv sync --extra voice
```

Set your OpenRouter key locally. Do **not** paste it into chat or commit it:

```bash
export OPENROUTER_API_KEY='...'
```

Optional model overrides:

```bash
export JEV_MODEL='~typesafe/jev-latest'
export JEV_DESKTOP_PLANNER_MODEL='openrouter/auto'
export JEV_DESKTOP_VISION_MODEL='openrouter/auto'
export JEV_DESKTOP_VERIFIER_MODEL='openrouter/auto'
export JEV_DESKTOP_WRITER_MODEL='openrouter/auto'
export JEV_DESKTOP_STT_MODEL='openai/whisper-1'
```

## Preflight

Before the first live run:

```bash
uv run python/cli.py --check
```

A healthy setup reports the advertised native/browser/perception capabilities
and whether Driver can see windows/apps and capture the desktop. On GNOME
Wayland, a degraded result normally points directly at Driver upgrade or the
WinRects helper.

## Tests

```bash
uv run python -m unittest discover -s python/tests -v
```

The unit suite is credential-free and does not operate the desktop.

## Text mode

Dry-run one decision without executing it:

```bash
uv run python/cli.py \
  "click the New Tab button" \
  --app Firefox \
  --provider openrouter \
  --json
```

Execute a real goal. By default this is **one agent goal**, not a planner-made
step list:

```bash
uv run python/cli.py \
  "Open Firefox, open a new tab, search for Alan Turing, and stop when the results are visible" \
  --provider openrouter \
  --act \
  --allow-foreground \
  --json
```

For tasks that genuinely benefit from explicit decomposition, opt in:

```bash
--planner
```

For debugging or high-assurance runs that should invoke the full verifier after
every mutation instead of only at `done`:

```bash
--verify-every-action
```

`--allow-foreground` does **not** force foreground input. The harness always tries
Driver's background route first and uses foreground only if Driver explicitly
returns a foreground escalation.

For typed browser downloads the harness uses `~/Downloads` when it exists. Set
a different already-existing approved directory with:

```bash
--download-root /absolute/path/to/downloads
```

Consequential actions stop for confirmation by default. For a trusted scripted
run you can pre-authorize that policy gate with:

```bash
--approve-consequential
```

## Voice mode

Start a continuous voice session:

```bash
uv run python/cli.py --voice --provider openrouter --allow-foreground
```

Add local spoken responses if `spd-say`, `espeak-ng`, or `espeak` is installed:

```bash
uv run python/cli.py --voice --speak --provider openrouter --allow-foreground
```

Voice mode listens until you finish speaking, sends the WAV to OpenRouter's
transcription endpoint, runs the same desktop agent, and asks for a spoken yes/no
before consequential actions. While a command is running, a parallel microphone
monitor accepts spoken `cancel`, `stop`, `stop now`, or `never mind`; the
agent stops before the next action (or immediately after the current atomic
Driver/provider call returns). Say `stop listening` or `goodbye` between
commands to exit the voice session. Ctrl-C remains an immediate local interrupt.

**Privacy note:** screenshot/chat requests use OpenRouter's no-data-collection +
ZDR routing controls. OpenRouter's transcription endpoint currently does not
support the same per-request ZDR/data-routing controls; using `--voice` sends the
recorded command audio to the configured transcription provider through
OpenRouter.

## Safety / execution invariants

1. Every element token/index belongs to one fresh Cua snapshot.
2. Every raw visual click requires the exact current Driver `capture_id`.
3. Jev receives IDs/descriptions, not Driver arguments or credentials.
4. Prepared typed text stays local to the executable candidate; Jev sees a text
   slot such as `text-1`, and field values are masked as `<set>` in Jev state.
5. One action is executed per observation, followed by reobservation.
6. `done` is not trusted by itself; the verifier must confirm the postcondition.
7. Repeated no-progress actions are suppressed instead of blindly retried; the
   planner is consulted only when planner mode was explicitly enabled.
8. Passwords/OTP/card-security-code/recovery/private-key/seed fields are not
   automatically filled.
9. Consequential actions require confirmation unless explicitly pre-authorized.
10. Foreground input is an explicit user authorization and only follows a Driver
    escalation from the background route.

## Known boundaries

This is a complete end-to-end **v1 harness**, not a claim of universal desktop
reliability. Apps with inaccessible custom canvases still depend on screenshot
availability and visual grounding. Generic completion verification is model-
assisted because arbitrary third-party applications do not expose a universal
postcondition API. Important irreversible outcomes should still use an
application-specific readback/oracle when one exists.

Cua Driver remains the body. This harness is the planning/decision/recovery layer;
it does not modify Driver's public action authority or embed Jev inside Driver.
