---
title: Bootstrap-Wiring Trace (Ghost-Wired Settings Gate)
description: How the setting-to-startup-trace gate detects ghost-wired settings whose consuming service is never started at boot.
---

# Bootstrap-Wiring Trace (Ghost-Wired Settings Gate)

A registered setting whose consuming machinery exists but is never instantiated at boot is **ghost-wired**: the value resolves cleanly through the [configuration precedence](configuration-precedence.md) chain, but no code path that reads it ever runs in default config. Import-graph traces find the consumer code but miss that its owning service is never started, so a static "find references" walk cannot distinguish a live consumer from a ghost-wired one.

`scripts/check_setting_to_startup_trace.py` is the standing gate. Pre-push and CI; it mirrors the `check_persistence_boundary.py` shape.

## What it catches

The lint detects two ghost-service patterns in lifecycle/app wiring, then matches settings to those ghosts via three matchers (first hit wins). Settings unrelated to a known ghost service pass silently; the lint never flags a setting in isolation.

**Ghost-service patterns:**

- **Hardcoded-None ghost.** A service variable `x: T | None = None` paired with a conditional `if x is not None: x.start()`. The guard always evaluates False, so any setting consumed inside the would-be service is dead at runtime even though the consumer code exists.
- **Factory-gated ghost.** A factory `build_x(config) -> T | None` whose `None` branch fires when a registered default-disabled flag is False: in default config the factory returns `None`, the start gate short-circuits, and every setting in the factory's gating namespace is dead.

**Fixing a ghost-wired service** means: drop the factory's early return (or the hardcoded `None`), construct the service unconditionally, gate the *behaviour* internally on the runtime flag, and wire a live `SettingsSubscriber` so operator changes take effect without restart. See `BackupService` (`backup/factory.py` + `backup/service.py` + `BackupSettingsSubscriber`) and `ApprovalTimeoutScheduler` (constructed in `api/app.py`, interval applied at boot via `_apply_security_timeout_interval` in `lifecycle_helpers.py`, live-tuned via `SecurityTimeoutSettingsSubscriber`) for end-to-end references.

**Setting -> ghost matchers** (run in order; first hit wins):

1. **Gating-namespace match** (factory ghosts only). Every setting whose `namespace` equals the factory's gating namespace is ghost-wired when the gating flag's registered default is False.
2. **Class-file containment match** (hardcoded-None ghosts only). A setting is ghost-wired iff its `key` appears as a substring in the ghost class's source file AND its `namespace` appears in that file's path.
3. **Direct ConfigResolver consumer match** (Pattern A; both ghost kinds). The lint scans the ghost class's source file for `ConfigResolver.get_*("<ns>", "<key>")` calls (resolving both string literals AND `SettingNamespace.X.value` references); if any (ns, key) matches a registered setting, that setting flags as ghost-wired. Catches **cross-namespace consumption**: a ghost class in `api/foo.py` that reads `engine.X` would not match either gating-namespace or class-file containment, but the direct ConfigResolver call surfaces it.

When debugging a Pattern A flag, search the ghost class's source for `ConfigResolver.get_*("<flagged_ns>", "<flagged_key>")` calls and verify whether the consumer should migrate to a real unconditionally-started service or whether the gating service should be wired at boot.

`compose_set=True` settings are skipped by design: they are read once through the bootstrap resolver before any service exists, so there is no live consumer to find, and the registry entry exists for `/settings` introspection. The skip is not a blanket pass. `check_setting_compose_backed.py` fails any `compose_set` key the shipped launchers do not actually pass, so a key cannot claim the flag to escape this scan.

## Suppression marker

Per-setting opt-out: append a trailing comment on the `_r.register(...)` closing line:

```python
_r.register(
    SettingDefinition(
        namespace=SettingNamespace.X,
        key="discoverability_only_setting",
        ...,
    )
)  # lint-allow: bootstrap-wiring -- explanation here
```

The justification after `--` is required and must be non-empty. It mirrors the `# lint-allow: persistence-boundary` contract.

## Baseline file

`scripts/setting_to_startup_trace_baseline.txt` freezes the pre-existing violations so the lint can ship without forcing the wiring fix in the same PR. Format: one entry per line, `<key>:<kind>:<owning_class>`, sorted lexicographically.

Lint behaviour:

- Pass when current violations are a subset of baseline.
- Fail (exit 1) listing only the new violations when current is not a subset of baseline.
- Warn (stderr) but pass when baseline contains stale entries (a fix landed and the violation no longer exists). Regenerate the baseline via `--update-baseline` once the wiring is fixed.

```bash
uv run python scripts/check_setting_to_startup_trace.py
uv run python scripts/check_setting_to_startup_trace.py --update-baseline
```

`--update-baseline` requires explicit user approval to commit the diff. Do not run it casually: the baseline is the lint's frozen authority.
