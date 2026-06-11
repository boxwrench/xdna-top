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

## Telemetry Backends

Prefer direct kernel interfaces over external tools where possible.

For AMD XDNA NPU telemetry, the preferred backend should be direct AMDXDNA DRM
IOCTL probing through `/dev/accel/*`. External tools such as `xrt-smi` should
remain compatibility fallbacks and sources for signals that are not yet exposed
through the direct backend.

Initial backend split:

- `amdxdna_ioctl`: device probing, DRM version, sensor support, NPU power, and
  column utilization where supported by the kernel
- `xrt_smi`: per-context PID, context ID, submissions, completions, and status
  until equivalent direct attribution is available
- degraded fallback: preserve zero-root operation and report exactly which
  signals are unavailable

Snapshot and record output should include backend provenance so reports can say
which interface produced each signal.

## Telemetry Inventory

The project should track potentially useful telemetry without trying to become a
general AMDGPU monitor. The inventory gives contributors and users a clear place
to see what is already captured, what appears available from related tooling,
and what belongs in a separate tool.

### In Scope Now

- iGPU busy percent from `amdgpu` sysfs
- iGPU power in watts from `amdgpu` hwmon sysfs
- NPU context PID from `xrt-smi examine --report aie-partitions`
- NPU context ID from `xrt-smi examine --report aie-partitions`
- NPU submission/completion counters from `xrt-smi examine --report aie-partitions`
- NPU derived active/idle state from counter deltas
- degraded flags when a source is missing or unreadable

### Available Elsewhere, Good Candidates

These are useful signals observed through `amdgpu_top` or direct AMDXDNA driver
probing. They are candidates for `xdna-top` only when they strengthen workload
evidence, snapshot reporting, or NPU+iGPU concurrency analysis.

- NPU firmware version
- AIE version
- AIE metadata such as columns and rows
- NPU clock metadata
- NPU task/resource limits
- NPU TOPS/resource info
- NPU power sensor
- NPU column-utilization sensor
- NPU read/write bandwidth sensors
- APU/iGPU memory clock and fabric clock
- iGPU GPU metrics and process memory attribution

### Likely Out of Scope

These are valuable, but they are already broad GPU-monitor territory. Prefer
linking users to `amdgpu_top` unless a specific signal is needed for
`xdna-top`'s workload-evidence story.

- full GRBM/GRBM2 counter views
- full GPU process/fdinfo dashboard
- complete VRAM/GTT monitoring UI
- GUI mode
- full video/media engine dashboard
- generic AMDGPU replacement features

### Issue Policy

Telemetry requests should name:

- the exact signal requested
- why it helps prove workload behavior or platform health
- where the signal is known to exist, if known
- whether the value needs provenance in `snapshot` or `record`

This lets users ask for missing signals while keeping the default answer honest:
use `amdgpu_top` for broad hardware monitoring, use `xdna-top` for Ryzen AI
workload evidence.

## Development Path

Keep the roadmap public and versioned, but keep risky backend work isolated.
The split should be:

- `main`: current release docs, stable commands, schema drafts, and issue links
- feature branches: one command family at a time, such as `feature/evidence-core`
  or `feature/workload-check`
- experimental backend branch: read-only driver probing before it graduates into
  released commands

Use a focused experimental branch for direct AMDXDNA work:

```bash
git switch -c exp/amdxdna-backend
```

Recommended sequence:

1. Keep current docs and schema changes on `main`.
2. Open public issues for the evidence core, direct backend, workload check,
   telemetry inventory, and themes.
3. Start `exp/amdxdna-backend` with read-only probes only.
4. Add a backend abstraction before adding more metrics.
5. Capture provenance for every signal.
6. Promote one signal at a time into `snapshot` and `record`.
7. Keep `amdgpu_top` linked as the general monitor rather than duplicating its
   full feature set.

For session-to-session pickup, use [HANDOFF.md](HANDOFF.md).

## Release Sequence

This should be the public roadmap. It is concrete enough to guide development,
but it still leaves implementation details open until each feature is designed.

### v0.1.x: Positioning and Schema

No major behavior change. This line is for getting the project framed correctly
before more commands land.

- document `xdna-top` as a workload-evidence tool, not a general AMDGPU monitor
- acknowledge `amdgpu_top` as the broad AMDGPU/APU monitor and inspiration for
  direct driver-facing telemetry
- publish the snapshot schema draft with `schema_version`
- publish the telemetry inventory and issue policy
- update the teaching guide and glossary for DRM, IOCTL, `/dev/accel`, AMDXDNA,
  XRT, and backend provenance

Release when the docs are coherent and the current TUI/`--json` behavior remains
unchanged.

### v0.2: Evidence Core

Build the pieces needed for reliable preflight, trial evidence, bug reports, and
update checks:

- `xdna-top snapshot`
- `xdna-top env-report`
- `xdna-top record`
- `xdna-top assert`

The direct AMDXDNA backend does not have to be complete for v0.2. The important
part is the artifact model: versioned JSON, backend provenance, degraded flags,
and non-zero exits for failed assertions.

Release when:

- snapshot JSON is stable enough for later compare/baseline work
- `record` writes JSONL with typed telemetry events
- `env-report` renders only captured snapshot facts
- `assert` names each check and prints observed values
- missing NPU/iGPU/XRT/sysfs signals degrade gracefully

### v0.3: Direct Backend and Upgrade Canary

Build comparison workflows on top of the snapshot schema:

- read-only `amdxdna_ioctl` backend for device probing and supported sensors
- `xdna-top compare`
- `xdna-top baseline`
- sharper degraded reasons for driver, device, sensor, and XRT shape changes
- more complete backend provenance in snapshots and recordings

Release when:

- a known-good snapshot can be saved and checked after kernel, BIOS, distro, or
  XRT updates
- compare output highlights meaningful platform drift instead of generic JSON
  noise
- direct AMDXDNA probes are optional and never break zero-root operation

### v0.4: Supervised Workload Checks

Build workload supervision after the lower-level evidence commands are stable:

- `xdna-top workload-check`
- optional OpenAI-compatible endpoint probing where useful
- request-window telemetry capture
- PID/context attribution in the output
- careful verdict language that reports measured counter movement, not certainty
  about request causality

This has the widest design surface: endpoint configuration, request timing
windows, context attribution, and careful verdict language.

Release when:

- the command can prove that the endpoint responded and measured NPU counters
  changed during the supervised window
- concurrent workload ambiguity is documented in the output and docs
- the JSON output is useful for automated evidence review

### v0.5: Community and Reports

This is the right place for lower-risk community features once the evidence core
exists.

- theme registry behind `--theme <name>`
- optional `XDNA_TOP_THEME`
- keep `lemonade-top` as a compatibility alias
- `THEMES.md` with screenshots and contribution rules
- richer HTML reports for captured telemetry
- more captures from additional Ryzen AI machines

Release when theme and report changes are data/config additions, not duplicated
entry points or renamed metrics.

### Version Timing

- Patch releases: docs, wording, bug fixes, theme additions, and small telemetry
  compatibility fixes.
- Minor releases: one new command family or one new backend capability.
- Avoid bundling more than one big concept into a minor release. For example,
  `snapshot`/`record`/`assert` can ship together as the evidence core, but
  `workload-check` should wait until that core is usable.
- Prefer shipping small, honest releases over holding features for a large
  milestone. A young monitoring tool gains trust by making each measured claim
  inspectable.

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
- direct AMDXDNA DRM/IOCTL probe status where available
- NPU BDF and device name from the best available backend
- NPU sensor support, power, and column utilization where available
- per-context PID/submission/completion data where available
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
xdna-top assert snapshot.json --require-npu --require-npu-sensors
xdna-top assert telemetry.jsonl --require-npu-activity
```

Each check should be named and should print the observed value next to the
threshold or requirement. Exit codes matter:

- `0`: requirements satisfied
- non-zero: one or more requirements failed

Example output shape:

```text
PASS require-npu-sensors: observed devices.npu.driver.supports_sensors=true
PASS require-context-source: observed backends.npu.signals.contexts=xrt_smi
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
- direct AMDXDNA IOCTL backend became unavailable
- NPU sensor support changed
- NPU BDF changed
- `xrt-smi` missing or report shape changed when it is needed for context data
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
- Prefer direct kernel/DRM interfaces over shelling out to external tools.
- Prefer composable primitives over one-off workflow commands.
- Do not infer NPU utilization percentages. Use context presence and
  submission/completion deltas, or clearly label direct sensor values such as
  column utilization.
- Degraded data should stay visible and machine-readable. A missing signal is a
  result, not a reason to invent one.
