# Jev desktop agent

A general desktop-control harness on top of **Cua Driver** and **TypeSafe Jev**.
The harness owns observation, candidate construction, local execution, recovery,
and a small amount of opt-in policy. Jev is a **categorical selector**: it sees a
bounded set of candidate IDs and descriptions plus bounded ordinary text from
editable fields, and returns exactly one supplied ID; it never invents tool
names, targets, coordinates, or arguments.

This implementation follows the boundary accepted in Cua RFC #3931: Driver owns
desktop authority; the harness constructs complete actions.

## Default behavior

An ordinary decision is **one fresh observation plus one Jev request**. Each
iteration reads the window inventory once, observes exactly one target window,
builds the complete local action pool, shortens it to a bounded Jev choice set,
asks Jev once, and executes at most one selected operation. The resulting-state
read *is* the next iteration's observation. There is no planner, no independent
verifier, no confidence gate, and no separate post-action observation.

`done` means Jev reports the goal complete; the harness does not independently
verify it. Sensitive-field filtering and consequential-action confirmation are
**opt-in** with `--confirm-actions`. Without it there is no harness policy gate
and Jev chooses from the offered candidates.

```text
voice or text command
        |
        v
Cua desktop overview (window inventory first; apps only when needed)
        |
        v
target window (one observation; app discovery/launch is lazy)
        |
        v
Cua get_window_state: accessibility tree, optional screenshot, optional capture_id
        |
        +--> Cua local visual parser when installed
        |       or
        +--> OpenRouter vision fallback (lazy, request-driven)
        |
        v
local candidate pool
  click/type/hotkey/scroll/visual + typed browser refs
  + operation bundles (fill-and-submit, browser search, new-tab search)
  + session operations (switch/app/inspect/prepare-text/more-actions)
  + done/reobserve/abstain
        |
        v
local shortlist -> at most 32 candidates
  (terminals, session ops, bundles, keyboard recovery and typing reserved;
   goal-relevant actions and a paged tail fill the rest)
        |
        v
one Jev request (OpenRouter Decisions API) -> one supplied ID
        |
        v
local stale-state validation --> Cua executes at most one operation
        |
        v
resulting state read == next iteration's observation
```

## What is implemented

- Persistent Cua Driver MCP session and one lifecycle session reused for the
  whole process (text or a multi-command voice session).
- One shared `OpenRouterClient` for vision, writing, and transcription plus one
  Jev chooser, all closed once at shutdown.
- GNOME/Wayland-safe child environment (`IsEnabled` accessibility advertisement;
  no `ScreenReaderEnabled` advertisement that can launch Orca).
- Native Wayland opt-in when a Wayland session is detected.
- Lazy, request-driven observation: the window inventory is read first; app
  inventory, desktop/window screenshots, and visual grounding are acquired only
  when semantic grounding is unavailable or Jev asks to inspect.
- Lazy app discovery and launch: the loop never launches an app on its own. Jev
  can select a session candidate that discovers or launches an app from Driver's
  real app inventory, never from a model-invented executable path.
- Locally built **operation bundles** Jev can select: fill-and-submit for a
  grounded editable control, browser address/type/submit, and new-tab plus
  address/type/submit when the goal asks for a new tab. Bundles are offers, never
  regex-triggered execution.
- Text visibility: Jev receives the **original user instruction** (it is not
  redacted) plus bounded ordinary observed editable field text
  (`observed_field_text`, at most 8 fields, 2000 characters each, with password
  and other sensitive fields excluded). Executable tool arguments and generated
  slot payloads stay local: choice descriptions carry only the slot id and
  purpose (`text-N` plus purpose), never the prepared or generated text itself.
  Common search/rename text is extracted locally, and Jev can select
  `prepare-text` to request additional ordinary text lazily from the writer
  model; because the writer produces that text, it is the generated text that is
  withheld from Jev, while the original instruction is still sent.
- Bounded ordinary field text is visible to the chooser: the Jev state includes
  `observed_field_text` for up to 8 editable fields, each value truncated to
  2000 characters, with password and other sensitive fields excluded. The text
  composer receives the same ordinary field text (up to 8 fields, 4000
  characters each) plus the current app/window and recent context.
- Bounded final shortlist: terminals, session operations, operation bundles,
  keyboard recovery, and typing are reserved before goal-relevant and remaining
  actions. Paging exposes the rest of the pool so a large tree does not lose its
  tail. `--max-candidates` defaults to 32 and accepts 4..32.
- Per-window **accessibility** observation on every iteration. Screenshots are
  lazy: a screenshot is captured only when semantic grounding is unavailable or
  Jev selects the inspect session candidate.
- Correct AT-SPI role handling including `push button`, `page tab`, `entry`,
  menu/list/radio/check controls, and action-advertising widgets.
- Optional Cua `parse_visual_regions` integration; OpenRouter vision is a lazy
  fallback for inaccessible/custom-drawn controls.
- Canonical screenshot coordinates with a configurable binding mode. Native
  Porter defaults to **Strict**, which requires capture-bound visual clicks.
  **Permissive** additionally allows Driver-supported canonical coordinates
  without a capture binding. No handcrafted OS scale conversion is used.
- Dynamic semantic actions, prepared-text typing, focused-window typing fallback,
  common hotkeys (including F2 rename), double/right click where requested,
  scrolling, visual actions, `done`, `reobserve`, and `abstain`.
- Opportunistic typed Chromium/Electron page control through exact
  `get_browser_state(..., semantic_v2)` refs. It is used only when Driver proves
  `binding_quality: exact` and `mutation_allowed: true`; unsupported browsers
  such as Firefox continue through native accessibility/visual control. The
  harness never auto-attaches to a personal browser profile or grants browser
  debugging consent.
- Large accessibility trees retain typing and keyboard recovery routes through
  both candidate budgets. Jev's bounded state view prioritizes candidate targets
  and selected controls; progress detection covers the full observed tree.
- OpenRouter Jev route through `POST /api/alpha/decisions` using
  `~typesafe/jev-latest` by default.
- OpenRouter writer and visual grounding using `openrouter/auto` by default.
  Common search/rename text is extracted locally instead of spending a
  writer-model call.
- In-process conversational context persists across text iterations and voice
  commands (the last eight summaries), so follow-ups work without restating the
  task.
- One operation executed per observation, followed by the resulting-state read;
  repeated no-progress actions are suppressed instead of blindly retried.
- Explicit Cua lifecycle-session management and automatic revival after a
  `session_ended` refusal.
- Explicit Driver/foreground boundary: background delivery is always tried
  first, and foreground is used only after Driver explicitly requests escalation
  and only with `--allow-foreground`.
- Bounded local download-resource memory: only the approved download directory
  (default `~/Downloads`) is watched, recursively to two levels, without reading
  file contents. Newly downloaded/renamed files can be handed back to exact
  `browser_set_input_files` refs without exposing absolute paths to Jev.
- Typed browser downloads to an approved directory and typed browser uploads of
  recently observed local files.
- Voice mode with VAD microphone capture, OpenRouter transcription, spoken
  results and confirmations, and optional non-blocking local TTS.

## Prerequisites

Use the newest Cua Driver available on the machine. Older Driver versions can
run the semantic path; full visual grounding and canonical coordinate clicks
require a Driver that advertises those capabilities. Canonical coordinates work
without an optional capture ID when the installed Driver supports them.

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

Optional overrides:

```bash
export JEV_MODEL='~typesafe/jev-latest'
export JEV_DESKTOP_VISION_MODEL='openrouter/auto'
export JEV_DESKTOP_WRITER_MODEL='openrouter/auto'
export JEV_DESKTOP_STT_MODEL='openai/whisper-1'
export JEV_DESKTOP_DOWNLOAD_ROOT='/absolute/path/to/downloads'
```

## Preflight

Before the first live run:

```bash
uv run python/cli.py --check
```

`--check` is the explicit diagnostics path: it reports Driver health,
advertised native/browser/perception capabilities, and whether Driver can see
windows/apps and capture the desktop. A healthy setup reports its capabilities;
on GNOME Wayland, a degraded result normally points directly at a Driver upgrade
or the WinRects helper. Normal startup does not run this health probe.

## Verification status

Porter has a dedicated native CI gate that compiles the Python sources, runs
credential-free runtime/voice/settings/visual-mode unit tests, installs the Qt
runtime dependencies, and loads both QML windows offscreen. That gate is kept
separate from the older harness suite.

The broader legacy harness suite still contains planner/verifier-era assertions
that predate the current direct Jev architecture, so it is not yet a clean
release gate. No measured live-desktop latency or reliability claim is made
from CI alone.

The legacy unit suite is credential-free and does not operate the desktop:

```bash
uv run python -m unittest discover -s python/tests -v
```

## Text mode

Dry-run one decision without executing it:

```bash
uv run python/cli.py \
  "click the New Tab button" \
  --app Firefox \
  --provider openrouter \
  --json
```

Execute a real goal:

```bash
uv run python/cli.py \
  "Open Firefox, open a new tab, search for Alan Turing, and stop when the results are visible" \
  --provider openrouter \
  --act \
  --allow-foreground \
  --json
```

`--allow-foreground` does **not** force foreground input. The harness always
tries Driver's background route first and uses foreground only if Driver
explicitly returns a foreground escalation.

By default the harness applies no policy gate. To opt in to sensitive-field
filtering and confirmation before consequential actions:

```bash
--confirm-actions
```

With `--confirm-actions`, a consequential action asks for confirmation in the
terminal; pre-authorize those actions for a trusted scripted run with:

```bash
--confirm-actions --approve-consequential
```

For typed browser downloads the harness uses `~/Downloads` when it exists. Set
a different already-existing approved directory with:

```bash
--download-root /absolute/path/to/downloads
```

## Porter native GUI (Aurora Dark)

The first native application shell is available through PySide6 + Qt Quick/QML.
It does **not** run a local web server and does not embed HTML.

Install the GUI extra:

```bash
uv sync --extra app
```

Launch Porter:

```bash
uv run --extra app python python/porter_app.py
```

The native shell currently provides:

- an Aurora Dark main window wired to the real resident `PorterRuntime`;
- a separate frameless compact command bar that is **not** forced always-on-top;
- a Porter circular status ring;
- live backend progress and command state;
- direct text command submission and cancellation;
- hide-on-close behavior for the main/compact windows;
- a system tray/status icon when the desktop exposes one;
- a resident backend that survives window hiding and owns Cua/Jev/OpenRouter
  exactly once;
- hands-free microphone listening enabled by default;
- local VAD with short speech pre-roll so fast speech is not clipped;
- automatic trailing-silence endpointing (0.55 s by default);
- STT submission without pressing a microphone button or Enter;
- spoken "stop"/"cancel"/"never mind" cancellation while Porter is working;
- live listening, microphone level, transcription and command state in QML;
- persistent non-secret settings through `QSettings`;
- OpenRouter and TypeSafe API keys through the OS credential/keyring service,
  never the QSettings file;
- real Models & Providers, Computer Control, and Voice & Audio settings pages;
- configurable Strict/Permissive visual click binding;
- optional consequential-action confirmation and foreground escalation;
- persistent download-root and voice/model defaults;
- XDG desktop-session autostart ("Start Porter when I sign in");
- live runtime reconfiguration: Apply restarts the backend stack inside the
  resident Porter process without closing the native application;
- a Wayland-safe global quick-bar shortcut through the XDG GlobalShortcuts
  portal, with `Ctrl+Alt+Space` as the preferred default trigger;
- a reverse-DNS desktop identity (`io.github.shehabyahya.Porter`) so modern
  portal implementations can identify the host application;
- real Shortcuts, Personalization, and Appearance pages;
- Aurora Dark accent, compact-bar idle/hover opacity, and animation preferences;
- a personalized native greeting that is kept out of Jev's control prompt;
- Linux desktop launcher/AppStream metadata and a reproducible Nuitka + dpkg
  build script for an installable `.deb`.

Hands-free listening requires `OPENROUTER_API_KEY` for transcription. Silence is
processed locally; only detected utterances are sent to STT. Start muted with
`--no-hands-free`, or toggle listening from the Porter tray menu or Voice &
Audio page.

The global shortcut uses the desktop portal rather than X11 key grabs. On
supported desktops the first registration may open the system shortcut
configuration dialog. If the portal is unavailable, Porter continues running
and reports the shortcut status in the Shortcuts page.

### Build an installable Debian package

On an Ubuntu/Debian build machine:

```bash
cd ~/cua/libs/cua-driver/examples/jev-desktop-agent
bash packaging/linux/build-deb.sh 0.1.0
```

The build uses Nuitka's PySide6 plugin, bundles the native Python/Qt app and its
QML/assets, then stages the desktop file, Porter ring icon, and AppStream
metadata into:

```text
dist/porter_0.1.0_<arch>.deb
```

Install the resulting package with:

```bash
sudo apt install ./dist/porter_0.1.0_amd64.deb
```

The installed launcher is `/usr/bin/porter`, backed by
`/opt/porter/porter`. Cua Driver remains a host prerequisite and is not
silently replaced or vendored by the Porter package.

For a headless QML load check:

```bash
QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software \
  uv run --extra gui python python/porter_app.py --smoke-test
```

The GUI architecture is:

```text
MainWindow.qml ──┐
                 ├── PorterViewModel ── PorterRuntimeThread
CompactBar.qml ──┘                         │
                                           └── asyncio
                                               ├── Cua Driver
                                               ├── Jev
                                               ├── OpenRouter
                                               └── AgentLoop
```

Qt stays on the main thread. The existing asynchronous backend owns one
dedicated worker-thread event loop for the application lifetime.

## Voice mode

Start a continuous voice session:

```bash
uv run python/cli.py --voice --provider openrouter --allow-foreground
```

Add local spoken responses if `spd-say`, `espeak-ng`, or `espeak` is installed:

```bash
uv run python/cli.py --voice --speak --provider openrouter --allow-foreground
```

Set how much trailing silence ends a spoken command (default `0.5` seconds,
valid `0.1`-`10.0`):

```bash
uv run python/cli.py --voice --voice-silence 0.8 --provider openrouter
```

Voice mode listens until you finish speaking, sends the WAV to OpenRouter's
transcription endpoint, and runs the same reusable agent and Driver session as
text mode. Results and requested confirmations are spoken when `--speak` is
enabled; `Listening` is printed rather than spoken before each command. TTS runs
in a worker thread and is awaited, so speech never blocks the asyncio event loop
or the microphone monitor.

While a command is running, a parallel microphone monitor accepts spoken
`cancel`, `stop`, `stop now`, or `never mind`; the agent stops before the next
action (or after the current atomic Driver/provider call returns). Spoken
cancellation cannot interrupt a network call already in flight. Say
`stop listening` or `goodbye` between commands to exit the voice session. Ctrl-C
remains an immediate local interrupt.

**Privacy note:** screenshot/chat requests use OpenRouter's no-data-collection +
ZDR routing controls. OpenRouter's transcription endpoint currently does not
support the same per-request ZDR/data-routing controls; using `--voice` sends the
recorded command audio to the configured transcription provider through
OpenRouter.

## Safety / execution invariants

1. Every element token/index belongs to one fresh Cua snapshot.
2. Visual clicks use the current screenshot's pixels; an optional capture ID is
   attached only when Driver provides one.
3. Jev receives IDs/descriptions, not Driver arguments or credentials.
4. Jev receives the original user instruction plus bounded ordinary
   `observed_field_text` (at most 8 fields, 2000 characters each, password and
   other sensitive fields excluded). Prepared and generated slot payloads stay
   local to the executable candidate; Jev sees only a slot id such as `text-1`
   and its purpose, never the prepared or generated text.
5. At most one operation is executed per observation, followed by the
   resulting-state read.
6. `done` yields status `completed` with the message "Jev reports the goal
   complete". It is Jev's assessment, not independent verification.
7. Repeated no-progress actions are suppressed instead of blindly retried.
8. Sensitive-field filtering and consequential confirmation apply only with
   `--confirm-actions`; `--approve-consequential` pre-authorizes the confirmed
   set.
9. Foreground input is an explicit user authorization and only follows a Driver
   escalation from the background route.

## Known boundaries

This is a complete end-to-end **v1 harness**, not a claim of universal desktop
reliability. Apps with inaccessible custom canvases still depend on screenshot
availability and visual grounding. There is no independent completion
verification in the default loop: `done` is Jev's own assessment. Important
irreversible outcomes should still use an application-specific readback/oracle
when one exists.

The large-tree search regression is tested with simulated desktop state and
the real candidate/shortlist/agent loop. New-tab and address-bar shortcuts use
Command on macOS and Control on Windows/Linux. These checks do not certify
native delivery on those platforms. On Linux, missing desktop display access
or an AppArmor denial of Snap Firefox accessibility calls can block observation
from an agent process even when terminal execution works. Verify the complete
search from the same desktop session and process environment that will run
voice commands.

Cua Driver remains the body. This harness is the decision/recovery layer;
it does not modify Driver's public action authority or embed Jev inside Driver.
