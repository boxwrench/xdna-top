# Roadmap

`xdna-top` reports observed Ryzen AI NPU and iGPU telemetry and preserves that
telemetry as trustworthy evidence. Every addition should improve the accuracy
of a real hardware observation, preserve it reproducibly, or satisfy a
demonstrated way users need to consume it.

The live TUI, snapshot/record artifacts, assertions, comparisons, baselines,
supervised workload checks, and Prometheus exporter are shipped. Release
history belongs in [CHANGELOG.md](../CHANGELOG.md); this document contains only
current priorities, triggers, and scope boundaries.

## Product Boundaries

- Report exactly what was observed, including unavailable and degraded signals.
- Preserve signal-level provenance. Device discovery, contexts, sensors, and
  power state may legitimately come from different sources.
- Prefer direct kernel interfaces where they expose the needed signal; retain
  `xrt-smi` for per-context PID and counter attribution.
- Preserve zero-root operation and a small base dependency set.
- Do not infer a generic NPU utilization percentage or request causality from
  coincident counter movement.
- Do not build provider frameworks, plugin registries, generalized layout
  systems, or other architecture for hypothetical future sources.
- Use `amdgpu_top` for broad AMDGPU monitoring. Keep this project focused on
  Ryzen AI workload evidence and concurrent NPU+iGPU observation.

## Now: Trust and Coherence

The active milestone is a stabilization release. It takes precedence over HTML
reports or a new feature family.

1. **Truthful NPU detection — done**
   - Set `devices.npu.detected` when any trusted source confirms the device:
     XRT identity, XRT contexts, or successful AMDXDNA ioctl identification.
   - Keep device detection separate from context availability.
   - Add an XRT-unavailable/ioctl-available fixture. `--require-npu` should pass
     while `--require-context-source` fails with a precise reason.
   - Preserve `backends.npu.primary` semantics for schema 1.0; prefer the
     existing `backends.npu.signals.*` fields for precise provenance.

2. **One coherent hardware sample — done**
   - Produce a fused reading and parsed contexts from one XRT observation per
     logical TUI frame, record sample, exporter scrape, or snapshot.
   - Reuse contexts already obtained by snapshot probing.
   - Keep the existing activity-delta behavior, including first observation and
     in-flight submissions, without introducing a provider abstraction.

3. **Finish existing integrations — done**
   - Feed the existing `read_npu_power()` result to the exporter so the
     documented clock metric is reachable; unavailable debugfs must remain a
     healthy degraded scrape.
   - Make `workload-check` emit JSON only on stdout and human explanation on
     stderr. Preserve endpoint-based exit semantics and add `schema_version` if
     the artifact is intended for persistent automation.

4. **Resolve the concrete presentation confusion — done**
   - Make clear that the theme gallery demonstrates palettes, not the live TUI
     layout.
   - Implement the selected stacked and NPU-only direct branches and test
     narrow/wide terminals.

5. **Validate degraded paths and real hardware**
   - **Done:** assert one XRT call and a shared context fixture for every
     sampling path.
   - **Done:** cover absent, unreadable, and unparsable direct power-state data.
   - Run idle, rapidly starting/stopping workload, XRT-failure, and
     debugfs-unavailable smoke tests on supported hardware.

This trust-and-coherence milestone is the v0.5.0 stabilization release. The
off-hardware implementation and automated validation form the release
candidate; the supported-hardware smoke pass is the remaining release gate.

## Next: Simplify the Product

- **Done:** promote [SNAPSHOT-SCHEMA.md](SNAPSHOT-SCHEMA.md) as the current schema 1.0
  contract and document the exact meaning or versioned deprecation path of
  `backends.npu.primary`.
- **Done:** remove the dormant gauge daemon/cache path after a final search of
  releases, docs, scripts, CI, benchmarks, and issues for external use.
- **Done:** discover sysfs once per process rather than persisting discovery by
  default; continue honoring an explicit `e0_sysfs.json` override.
- **Done:** retain `--bench-dir` for the contention benchmark and explicit
  sysfs-path override workflow, with help text that states that purpose.
- **Done:** archive completed implementation plans and the stale session handoff as
  historical context. Do not create another current-state document.
- Complete a real-hardware XRT-absent/direct-backend-present validation when
  such a test environment is available.

Public schema fields or CLI flags must not be removed in the same patch as the
lower-risk correctness work. Compatibility changes need explicit fixtures and
release notes.

## Triggered Later

These items are intentionally dormant until their trigger occurs.

- **HTML reports:** at least two concrete users or workflows need a
  self-contained shareable report that Markdown and Grafana do not satisfy.
- **Configurable poll rate:** a measured overhead problem, driver limitation,
  or documented sampling requirement exists.
- **Per-context live history:** a user needs history inside the TUI and
  `record` or Prometheus cannot satisfy the workflow.
- **Additional APU support:** a contributor supplies access, a real capture, or
  a committed tester for the hardware.
- **New direct sensor fields:** a tested kernel exposes the corresponding ioctl
  or sysfs interface and the signal strengthens workload evidence.
- **PID-specific watch mode:** `workload-check` and record/assert cannot answer
  a documented process-attribution use case.
- **Additional themes:** accept small, claims-accurate community contributions;
  do not schedule candidate palettes as milestone work.

## Parking Lot

- Presentation modes beyond the concrete open layout request.
- Self-contained monitoring-stack examples.
- Experimental research visualizations.
- Community theme ideas without a contributor.

Generic evaluation tooling, endpoint probing beyond `workload-check`, wiki or
vault tooling, and evidence-process linters are separate product ideas. They do
not belong in this repository roadmap and must not influence its architecture.

## Research and Documentation

Retain raw captures, negative sensor probes, reproduced experiments, benchmark
measurements, limitations, and withdrawn claims. Label research documents as
one of: observed capture, reproduced experiment, interpretation, design idea,
implemented historical plan, or superseded plan.

Use one source for each purpose:

- [README.md](../README.md): current public capability and near-term direction
- this roadmap: active priority, triggers, and scope boundaries
- [CHANGELOG.md](../CHANGELOG.md): delivered release history
- [devlog.md](../devlog.md): chronological implementation and hardware evidence
- [SNAPSHOT-SCHEMA.md](SNAPSHOT-SCHEMA.md): artifact contract

## v0.5.0 Release Gate

- Direct ioctl evidence can truthfully detect an NPU without XRT contexts.
- Each logical sample uses one internally consistent XRT/context observation.
- Exporter clock metrics are either reachable from production or no longer
  promised.
- `workload-check` stdout parses directly as JSON.
- Documentation agrees on the active milestone, schema, layout behavior, and
  shipped capabilities.
- Unit tests cover the degraded backend matrix.
- The package builds and installs, generated documentation is current, and the
  supported Python test matrix passes.
- Supported NPU hardware completes the idle, workload-churn, XRT-failure, and
  debugfs-unavailable regression smoke pass before the version bump and tag.
