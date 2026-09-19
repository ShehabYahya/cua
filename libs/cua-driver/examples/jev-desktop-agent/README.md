# Jev desktop agent scaffold

This directory is an experimental scaffold for turning Cua Driver's bounded
`jev-use` recipe into a general native-desktop loop. It follows the boundary in
RFC #3931: Cua Driver owns observation and execution, the application owns the
candidate table and policy, and TypeSafe Jev may select only a supplied candidate
ID.

The scaffold is intentionally narrower than the end goal. Phase 1 establishes a
persistent observe → build candidates → choose → validate → execute → reobserve
loop over accessibility elements. It does not yet claim task completion, perform
LLM planning, generate arbitrary text, use screenshot perception, or accept voice
input.

## Current shape

```text
natural-language goal
        |
        v
Cua Driver observation
        |
        v
deterministic candidate builder
        |
        v
TypeSafe Jev chooses one supplied ID
        |
        v
validate against immutable table
        |
        v
execute at most one Cua action
        |
        +----> fresh observation ----+
```

Executable tool names and arguments stay local. Jev receives the goal, a compact
semantic observation, recent outcomes, and candidate IDs/descriptions only.
Prepared quoted text also stays local; Jev sees a slot ID rather than the text
value.

## Files

- `python/contracts.py` — immutable observations, candidates, decisions, and the
  chooser/driver interfaces.
- `python/candidates.py` — bounded dynamic action generation from the current
  accessibility snapshot.
- `python/driver.py` — persistent Cua Driver MCP adapter.
- `python/jev_adapter.py` — bounded TypeSafe chooser.
- `python/loop.py` — one-action-per-observation agent loop.
- `python/planner.py` — Phase-1 pass-through planner interface.
- `python/writer.py` — local prepared-text slots; no model-generated text yet.
- `python/verifier.py` — explicit placeholder for independent goal verification.
- `python/cli.py` — dry-run-by-default entry point.

## Run tests

```bash
cd libs/cua-driver/examples/jev-desktop-agent
python -m unittest discover -s python/tests
```

The tests are credential-free and do not touch the desktop.

## Try the live semantic loop

Install the same prerequisites as `../jev-use`, set `TYPESAFE_API_KEY`, and keep
Cua Driver running in the target graphical session. Start with dry-run:

```bash
uv sync
uv run python/cli.py "click the Downloads button" --app Firefox
```

Only add `--act` after inspecting the selected action:

```bash
uv run python/cli.py "click the Downloads button" --app Firefox --act --max-steps 3
```

The live path currently refuses degraded/truncated semantic observations instead
of guessing from pixels. It also has no goal-completion verifier yet, so an
acting run normally ends at the step budget unless Jev abstains or becomes
uncertain.

## Next increments

1. Add independent goal/progress verification and stop conditions.
2. Add hierarchical/fan-out action selection for windows with more than 30 useful
   controls instead of relying only on lexical preselection.
3. Add browser-specific candidate generation from DOM refs.
4. Add `cua.visual_regions_v1` fallback with exact capture binding.
5. Add a planner for compound goals and a writer that produces local text slots.
6. Add recovery for dialogs, no-op actions, stale state, and repeated decisions.
7. Add streaming STT, cancellation, and optional TTS as input/output adapters.

Do not move Jev credentials, provider logic, or free-form executable action
generation into Cua Driver. Do not let the chooser invent coordinates, tool
names, target IDs, or arguments.
