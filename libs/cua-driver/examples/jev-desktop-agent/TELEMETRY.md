# Local telemetry and Laya dataset preparation

Telemetry is **off by default**. This adds an observational collection and offline
label/export layer to Porter, not another planner, verifier model, executor, or
automatic training process. No extra provider calls or desktop captures are made.

## Enable capture

Quit the resident Porter process before relaunching with environment settings;
opening a second launcher only activates the existing process.

```sh
# Counts, bounded error codes, and timings; no training examples.
PORTER_TELEMETRY_MODE=operational porter

# Local, aggressively redacted diagnostic decision traces; explicit consent.
PORTER_TELEMETRY_MODE=training PORTER_TELEMETRY_TRAINING_CONSENT=1 porter

# Disable on the next runtime start.
PORTER_TELEMETRY_MODE=off porter
```

These variables also apply to the Python GUI/CLI entry points. Programmatic
clients may supply `PorterRuntimeConfig(telemetry=TelemetryConfig(...))`; an explicit
`TelemetryConfig()` overrides the environment and keeps capture off. There is no
new GUI settings switch in this change. Invalid environment configuration disables
capture without blocking Porter. `runtime.telemetry_status` and the existing
runtime diagnostics expose mode, queue depth, dropped events, and writer failure.

Default storage is `${XDG_STATE_HOME:-~/.local/state}/porter/telemetry/traces.sqlite3`.
`PORTER_TELEMETRY_DIR` overrides the directory. It must be a new or owner-only
non-symlink directory. The directory is mode 0700; files are mode 0600. Capture is
local only: no upload, sync, hub publishing, crash-report integration, or network
export. Use an encrypted home/volume for machine-appropriate encryption at rest;
this code does not implement encryption or a key-management system.

## Recorded data

Every event has schema, collection mode, UTC/monotonic time, session, command,
decision, and sequence IDs. Dispatches carry selected action, physical attempt,
macro child index, foreground retry/parent, snapshot linkage, and observation age.
Source-file digests are calculated on the writer thread where source is available;
packaged/unavailable source revisions remain null. Builds may set the immutable
40-hex-character `PORTER_BUILD_COMMIT`. No guessed model/tokenizer revisions,
inference timings, logits, or token counts are substituted for unavailable data.

Training capture adds an episode-local structural projection: aliased goal,
heuristic intent constraints (including negated submission), role/target/focus
state, bounded pre-suppression pool, exact shown order and request-local mapping,
page, suppressions, provider probabilities/confidence, and selected versus actual
operations. Observation results are reused from the existing loop. The pool is
capped at 512 candidates, shown choices at 32, elements at 192, and macro children
at 32 per level. Omissions and unknown fields are explicit; a cap is not silently
presented as a complete training input. Suppressed session options absent from the
original pool are recorded as IDs/reasons, not fabricated alternatives.

Free text, descriptions, app/window/tab identifiers, and labels become local
aliases. Raw message/field/clipboard values, paths, URLs, credentials, screenshots,
audio, provider headers, and exception strings are not persisted. The reverse
alias map exists only in memory and resets at command boundaries. STT events carry
random utterance linkage and elapsed time, with quality/revision unknown. Voice
cancellation remains cancellation, not an inferred transcription correction.

Tool acceptance, observed change, step verification, and task verification are
separate. `done`/`completed` remains a model report, not verified success. A generic
transport failure has **unknown delivery**; successful macro children and explicit
refusals are separate events. Foreground retries do not create extra logical
children. Independent verification defaults to unknown, including after a popup
changes the screen.

## Independent labels and export

Run maintenance from this example's directory **after stopping Porter**:

```sh
python python/export_laya.py --directory "$HOME/.local/state/porter/telemetry" export
python python/export_laya.py --directory "$HOME/.local/state/porter/telemetry" export --json-strings
python python/export_laya.py --directory "$HOME/.local/state/porter/telemetry" label DECISION_ID reviewed-label.json
python python/export_laya.py --directory "$HOME/.local/state/porter/telemetry" delete-command COMMAND_ID
```

The object-valued export uses `state`, `questions`, and `gold`. `--json-strings`
JSON-encodes those three fields for notebook loaders expecting strings. Never pass
the entire telemetry envelope to the model. Predictions and post-action evidence
are not copied into the pre-decision input.

`telemetry_labels.py` provides pure, content-free evidence checkers for typing,
submission, focus, existing-tab reuse, and multi-requirement tasks. Supply actual
before/after facts and an explicitly settled interval; no hidden desktop polling
or model judgement runs inside these functions. Text equality is computed only in
memory. Delayed effects remain pending; a cleared composer or successful Enter
call alone cannot establish submission success. These functions are primitives for
an independent fixture/browser verifier, **not a live Firefox DOM integration**.

Choice labels are separate, append-only revisions. A reviewer supplies captured
local candidate aliases (`c...`), independently reviewed evidence IDs, source,
correction scope, and input/rights attestations. Evidence may be external; its IDs
are references, not cryptographic proof of truth. Example label shape (replace all
IDs and review the original decision; this is not a seed training example):

```json
{
  "source": "human_review",
  "acceptable_ids": ["c7"],
  "evidence_refs": ["00000000000000000000000000000000"],
  "status": "verified",
  "failure_category": "unknown",
  "correction_scope": "original_decision",
  "input_reviewed": true,
  "training_rights_reviewed": true
}
```

New revisions supersede old revisions for export; `status: "retracted"` removes
eligibility without rewriting history. Changed intention/transcription corrections
are not original-state gold. Multiple acceptable actions require an explicitly
reviewed soft target; there is no arbitrary uniform fallback. Candidate-generation,
shortlisting, and choice failures are classified separately.

## Deliberate eligibility boundary

**The existing Jev chooser is not replaced by Laya in this change. Its captured
input is `sanitized_reconstruction`, not exact replay, and its provenance remains
Jev-derived. Such traces are never exported as Laya training rows.** Unknown custom
providers are also ineligible. Redaction can remove distinctions needed to choose
correctly; a capture toggle cannot establish that those distinctions survived.

A future Laya adapter must use the approved formatter *before inference*, capture
that exact representation, and report token audits from its actual pinned builder,
tokenizer and checkpoint. The exporter currently validates the conservative
`porter-structural-v1` schema, rejects unreviewed history, and requires independent
labels, complete option markers/order, pinned revisions, explicit non-Jev/non-teacher
provenance, reviewed semantics/rights, and a closed loss-free trace. The included
synthetic fixtures test that contract; they are not measured Laya token audits or
an approved seed dataset. No live Laya adapter, tokenizer instrumentation,
raw-logit hook, model download, training, calibration, or promotion is included.

An export contains a manifest with session/command IDs, capture times, and exact
state-duplicate groups. It stays chronological and unsplit. Perform downstream
whole-session/command/near-duplicate grouping and chronological holdout before
training; this module does not create evaluation splits or claim near-duplicate
semantic detection. No data volume is treated as a guarantee of improvement.

## Storage failure, retention, and deletion

The producer queues already-redacted bounded events without disk I/O. A dedicated
writer owns SQLite and a nonblocking single-writer lock. Defaults are 128 queued
events, 256 KiB/event, 64 MiB store/export budget, 14-day retention, and a 0.5-second
shutdown flush; programmatic configuration can lower or raise bounded limits.
A full queue drops the new event and invalidates the entire session. Projection,
thread-start, filesystem, and writer failures do not become control failures.
A crash/unflushed session cannot produce a complete training row. Size exhaustion
stops capture; disk usage can temporarily exceed the budget by one bounded event
and database/journal overhead, not grow indefinitely with further events.

Retention removes old sessions and their labels/managed exports. Episode deletion
removes its events, all label revisions, and every managed export containing it.
Exports are registered before bytes are written so interrupted exports remain
associated with their source episodes. Arbitrary copies made elsewhere and already
trained checkpoints cannot be recalled by source deletion. Treat model artifacts
as sensitive and retrain from an approved dataset when necessary.

## Validation

```sh
python -m unittest discover -s python/tests -p 'test_telemetry*.py' -v
```

Coverage includes opt-in/default-off behavior, privacy, bounded queues, dropped and
incomplete traces, independent labels, order-preserving targets, strict provenance
and future-state rejection, deletion, unchanged action/observation counts, scoped
concurrent instrumentation, partial macros, and foreground retries. Existing Porter
and Jev suites remain the regression gates; no verification gate is weakened.
