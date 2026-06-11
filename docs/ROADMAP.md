# Roadmap

`xdna-top` started as a live terminal monitor for AMD Ryzen AI NPU and iGPU
telemetry. The next phase is to make that telemetry usable as evidence:
preflight checks, update canaries, eval logs, and workload proofs.

The roadmap is schema-first. Four commands are views over the same artifact:

- `snapshot`: capture JSON
- `env-report`: render Markdown from that JSON
- `compare`: diff two snapshots
- `baseline`: diff a new snapshot against a named local snapshot

Getting the snapshot schema right makes those commands thin and keeps future
comparison compatible.

## Product Direction

The core promise should stay narrow and testable:

> `xdna-top` reports observed Ryzen AI NPU and iGPU telemetry, and makes that
> evidence reproducible across scripts, reports, and update checks.

This means future features should prefer machine-readable evidence over pretty
output. The TUI remains useful for humans, but new commands should be designed
for scripts, CI gates, devlogs, bug reports, and eval artifacts.

Claims precision applies to every verdict. Commands should report exactly what
they measured, such as:

> Contexts owned by PID 1234 incremented submissions by 42 during the request
> window.

They should avoid stronger claims such as:

> The NPU ran your request.

Concurrent workloads can increment counters too. Per-context PID attribution is
therefore part of the evidence model, not a cosmetic detail.

## Release Sequence

### v0.2: Evidence Core

Build the pieces needed for reliable preflight and trial evidence:

- `xdna-top snapshot`
- `xdna-top env-report`
- `xdna-top record`
- `xdna-top assert`

### v0.3: Upgrade Canary

Build comparison workflows on top of the snapshot schema:

- `xdna-top compare`
- `xdna-top baseline`

### v0.4: Supervised Workload Checks

Build workload supervision after the lower-level evidence commands are stable:

- `xdna-top workload-check`

This has the widest design surface: endpoint configuration, request timing
windows, context attribution, and careful verdict language.

## Commands

### `xdna-top snapshot`

Capture a point-in-time platform and telemetry record.

Example:

```bash
xdna-top snapshot --out bench/platform.json
```

The snapshot should include:

- `schema_version`
- capture metadata and command line
- kernel version
- relevant command versions where available
- `/dev/accel/accel*` device presence
- NPU BDF and device name from `xrt-smi examine`
- raw or summarized `xrt-smi examine --report aie-partitions` shape
- iGPU sysfs telemetry paths
- one fused telemetry reading
- degraded flags and reasons

Why first: every other evidence feature needs a stable record format.

See [SNAPSHOT-SCHEMA.md](SNAPSHOT-SCHEMA.md) for the draft schema.

### `xdna-top env-report`

Render a concise report from a captured snapshot.

Example:

```bash
xdna-top env-report bench/platform.json --markdown
```

This should generate a paste-ready section for devlogs, bug reports, and eval
notes: platform, driver/device visibility, degraded flags, one fused reading,
and exact command versions.

It should not probe the system again. It should summarize captured facts from
the snapshot so reports remain reproducible.

### `xdna-top record`

Record telemetry as JSONL over a time window.

Example:

```bash
xdna-top record --duration 60 --interval 0.2 --out bench/telemetry.jsonl
```

Each line should be a typed event:

```json
{"type":"telemetry","schema_version":"1.0","ts":123.4,"reading":{},"contexts":[]}
```

This mostly formalizes what the gauge daemon and existing one-shot JSON logging
already do, with a stable stream format for eval evidence.

### `xdna-top assert`

Provide machine-readable pass/fail checks for scripts and CI.

Examples:

```bash
xdna-top assert snapshot.json --require-npu --require-xrt
xdna-top assert telemetry.jsonl --require-npu-activity
```

Each check should be named and should print the observed value next to the
threshold or requirement. Exit codes matter:

- `0`: requirements satisfied
- non-zero: one or more requirements failed

Example output shape:

```text
PASS require-xrt: observed xrt_smi.available=true
FAIL require-npu-activity: observed submission_delta=0, required >0
```

### `xdna-top compare`

Compare snapshots without drowning the user in generic diff noise.

Example:

```bash
xdna-top compare before.json after.json
```

The default comparison should emphasize platform and telemetry changes that
affect trust in an experiment:

- kernel changed
- `amdxdna` or accel device missing
- `/dev/accel/accel0` changed or disappeared
- NPU BDF changed
- `xrt-smi` missing or report shape changed
- iGPU sysfs paths changed
- telemetry became degraded when it was previously healthy

Profiles can tune policy later, but they should not fork the data model.

### `xdna-top baseline`

Wrap snapshot and compare into a named local workflow.

Examples:

```bash
xdna-top baseline save known-good
xdna-top baseline check known-good
```

Baseline names in public docs should stay generic, such as `known-good` or
`post-kernel-update`.

Why later: this is a workflow convenience over snapshot and compare, not a new
measurement capability.

### `xdna-top workload-check`

Run a short supervised request and report whether measured NPU context counters
changed during the request window.

Example:

```bash
xdna-top workload-check \
  --models-url http://127.0.0.1:13306/v1/models \
  --chat-url http://127.0.0.1:13306/v1/chat/completions \
  --model llama3.2:1b \
  --out bench/workload-check.json
```

The result should distinguish:

- server exists
- model endpoint responds
- chat request completes
- NPU contexts appear
- expected PID owns one or more contexts, when PID attribution is available
- submissions/completions increment during the supervised request window

The verdict must use measured language. For example:

```text
Observed PID 1234 context 1 submission_delta=42 completion_delta=42 during request window.
```

This is stronger and more honest than claiming the request definitely ran on the
NPU, because unrelated concurrent workloads can move counters.

## Additional Ideas

### Eval artifact diff viewer

Generate a single HTML report from eval artifacts that shows:

- raw transcript
- compacted or summarized output
- facts ledger
- which tracked facts survived
- which tracked facts dropped
- the compaction or transformation step where each fact changed

This is a companion to diagnose-before-patching workflows. When an eval fails,
the goal is to turn artifact inspection from manual JSON spelunking into a
direct view of what changed.

### Eval cassette replayer

Record real model or service responses during a hardware-backed run, then replay
them deterministically in sandboxed CI.

This would make evals re-runnable anywhere and make two runs diffable even when
the original hardware or service is unavailable. It is adjacent to, but separate
from, `xdna-top` telemetry recording.

### Vacuity guard

Make every eval declare proof that the tested mechanism actually fired.

Each eval should have both:

- pass criteria
- mechanism-fired criteria

A trial that satisfies pass criteria without exercising the target mechanism
should be invalid, not passing. This pattern applies broadly to telemetry,
memory, compaction, routing, and tool-use evals.

### Wiki doctor

Build a vault linter for LLM-maintained Markdown or Obsidian-style knowledge
bases.

Potential checks:

- dead wikilinks
- orphan pages
- unresolved supersedes chains
- stale drafts in inbox folders
- naming violations
- schema violations

This could stand alone as "CI for LLM-maintained knowledge bases."

### Vault graph visualizer

Render a knowledge-base link graph for review:

- orphans
- hubs
- supersession lineage
- disconnected clusters
- high-risk stale branches

This can start as an HTML report mode for a wiki doctor rather than a separate
tool.

### Endpoint prober

Probe an OpenAI-compatible server and report:

- available endpoints
- available models
- basic request success/failure
- context limits where discoverable
- rough tokens per second
- whether a supervised request coincided with measured NPU context deltas

This overlaps with `xdna-top workload-check`, but may be useful as a sibling
tool focused on serving-layer diagnostics.

### Evidence linter

Lint project trackers and implementation plans for evidence hygiene:

- completed rows have evidence links
- verdict rows are append-only
- devlog entries exist for closure
- required artifact paths exist
- failure diagnoses are recorded before patches

This is process tooling, but it reinforces the same evidence-first principle as
the telemetry roadmap.

### Theme registry

Themes are a low-priority community feature and a good source of first
contributions. Before adding more themes, generalize the implementation:

- add a theme registry
- support `--theme <name>`
- optionally support `XDNA_TOP_THEME`
- keep `lemonade-top` as a compatibility alias
- make each new theme a small data entry, not a new command

Themes must only affect colors, borders, header art, and glyph choices. They
must not rename metrics, states, units, counters, or measured values. A
screenshot in any theme should stay claims-accurate.

Candidate themes:

- `paper`: high-contrast, colorblind-safe, print-friendly documentation theme
- `halo`: deep navy/silver, inspired by Strix Halo without using marks
- `fabric`: teal tile-grid motif, reflecting the NPU's spatial dataflow fabric
- `phosphor`: green monochrome CRT terminal theme
- `amber`: amber CRT terminal theme
- `team-red`: generic red/black hardware-enthusiast theme
- `lime`: citrus sibling to `lemonade`
- `grapefruit`: citrus sibling to `lemonade`

Potential community docs:

- `THEMES.md`
- screenshot gallery
- "contribute a theme" good-first-issue template

This is intentionally behind the evidence-core work. Themes are useful for
community ownership, but they should not displace snapshot, record, assert, or
compare.

### Event markers

Allow scripts to annotate telemetry logs.

Example:

```bash
xdna-top record --out bench/trial.jsonl &
xdna-top mark --out bench/trial.jsonl "trial-1-start"
xdna-top mark --out bench/trial.jsonl "compaction-start"
xdna-top mark --out bench/trial.jsonl "trial-1-end"
```

Marker lines should use the same JSONL stream:

```json
{"type":"mark","schema_version":"1.0","ts":124.0,"label":"compaction-start"}
```

### PID-aware watch

Track whether a specific process owns NPU contexts.

Example:

```bash
xdna-top watch --pid "$(pgrep -f 'model-server')"
```

If XRT exposes only PID, resolving command names and tracking context deltas per
PID is still enough to prove whether the expected process is exercising the NPU.

### Public issues

Once the v0.2 plan is settled, open one GitHub issue per command. A public
roadmap on a young project invites contributors and makes the project read as
active without overpromising implementation dates.

## Invariants

- Keep JSON schemas explicit and versioned.
- Preserve zero-root operation.
- Preserve graceful degradation for missing drivers, commands, or sysfs paths.
- Prefer composable primitives over one-off workflow commands.
- Do not infer NPU utilization percentages. Use context presence and
  submission/completion deltas.
- Degraded data should stay visible and machine-readable. A missing signal is a
  result, not a reason to invent one.
