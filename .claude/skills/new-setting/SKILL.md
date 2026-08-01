---
description: "Add a settings knob end to end: definition, precedence category, live-apply wiring or a compose-set declaration, and dashboard visibility"
argument-hint: "<namespace>.<key> [one-line purpose]"
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
  - Grep
  - Glob
  - AskUserQuestion
---

# Add a setting

`synthorg new` scaffolds services, repositories, tools and controllers. It does
not scaffold settings, and a setting is the densest change in the tree for gate
count: three separate pre-push gates have an opinion about it, plus the
No-Hardcoded-Values rule that usually motivates adding one in the first place.
Getting any of them wrong is discovered at push, on the push budget.

Work through the phases in order. Do not skip Phase 1: the precedence category
determines everything downstream, and it is the one decision that cannot be
mechanically fixed later.

## Phase 1: Decide the precedence category

Read [configuration-precedence.md](../../../docs/reference/configuration-precedence.md)
before choosing. The three categories are not interchangeable:

| Category | Precedence | Use when |
| --- | --- | --- |
| Cat-1 | DB > env > code default | An operator should be able to change it at runtime. **Default choice.** |
| Cat-2 | env > code default | Deployment-shaped, no DB row (transport, process topology). |
| Cat-3 | env only | A bootstrap secret needed before the DB exists. |

If the answer is not obvious from the setting's purpose, ask the user with
`AskUserQuestion` and lead with Cat-1 as Recommended: it is the only category
that produces a knob an operator can actually turn without a redeploy.

## Phase 1b: Get the plan accepted before writing anything

Planning is MANDATORY. Present the chosen category, the namespace and key, the
default, and where the consumer will read it, then wait for accept or deny. The
category is the one decision that cannot be mechanically fixed later, so it is
also the one worth confirming before any of the five downstream edits exist.

## Phase 2: Register the definition

Settings live in `src/synthorg/settings/definitions/<namespace>.py`, one module
per `SettingNamespace` enum value. The `check_settings_namespace_complete` gate
enforces that pairing, so a new namespace means a new enum value **and** a new
module in the same change.

```python
_r.register(
    SettingDefinition(
        namespace=SettingNamespace.<NAMESPACE>,
        key="<key>",
        type=SettingType.<TYPE>,
        default="<string>",          # every value is stored as a string
        description="<what an operator reads in the dashboard>",
        group="<UI grouping label>",
        level=SettingLevel.BASIC,    # ADVANCED hides it behind disclosure
    )
)
```

Points that bite:

- `default` is a **string** regardless of `type`; coercion is driven by `type`.
- `description` and `group` are `NotBlankStr` and are what the dashboard renders.
  The settings UI is generated from the registry, so a well-formed definition is
  automatically visible: there is no separate UI registration step, and a vague
  `description` is the only way to end up with an unusable knob.
- `sensitive=True` encrypts at rest and masks in the UI. Use it for anything
  credential-shaped.
- `SettingType.MODEL_REF` rejects a provider-less value at write time, per the
  Explicit Provider Binding rule. Never introduce a bare model-name string
  setting.
- Numeric bounds (`min_value` / `max_value`) must be finite and ordered, and
  `enum_values` is required when `type` is `ENUM`.

## Phase 3: Make it live, or make it compose-set

A setting is one or the other; there is no third state and no restart control
to fall back on. Default to live, and pick the seam the consumer allows:

- read it per call through the resolver, or
- take it from a bridge snapshot, or
- add a `set_*()` setter plus a subscriber, or
- add the key to `RuntimeReloadSettingsSubscriber` when the value is baked into
  something `build_runtime_services` rebuilds, or
- add it to a `SubsystemSpec.settings` tuple when a reconciled subsystem bakes
  it in at activation.

`compose_set=True` is only for what the running process genuinely cannot change
about itself (the socket it already bound, an image the CLI verified, a trust
anchor resolved before the settings backend exists). It also obliges you to pass
the env var from every shipped launcher that starts the process reading it, in
the same change: both `cli/internal/compose/compose.yml.tmpl` and
`docker/compose.yml` for a backend setting, or `cli/cmd/worker_start.go` for a
worker-only one. `check_setting_compose_backed` fails a compose-set key the
deployment does not actually pass, so the flag cannot mean "not wired up".

If the setting weakens security when written, it additionally needs the
confirm-and-reason guardrail in `settings/write_governance.py`.

## Phase 4: Wire the consumer so it is not a ghost

`check_setting_to_startup_trace` fails a setting whose only consumer lives in a
service that boot never instantiates. Registering a definition and reading it
from a class that nothing constructs produces a knob that silently does nothing.

Trace the consumer to an actual boot path in
[api-startup-lifecycle.md](../../../docs/reference/api-startup-lifecycle.md).
If the owning service is gated behind a default-disabled flag, the gate treats
every setting in the gating namespace as ghost-wired: that is the gate working,
not a false positive.

Read the value through `ConfigResolver`. Never reach for `os.environ.get`
outside the bootstrap allowlist; `check_no_os_environ_outside_bootstrap`
enforces that, and a Cat-1 setting read from the environment silently loses the
DB layer that makes it operator-changeable.

## Phase 5: Remove the literal that motivated the setting

If this setting exists to retire a hardcoded number, delete the literal in the
same change. Leaving both means the gate passes while the value an operator sets
is ignored, which is worse than the original hardcoded value because it is now
also a lie. The exceptions the No-Hardcoded-Values gate allows are 0/1/-1, HTTP
status codes, hex masks, powers of two, and module-level
`NAME: Final[...] = literal`.

## Phase 6: Verify

Run only the gates this change can affect, scoped rather than whole-tree:

```bash
uv run python scripts/check_settings_namespace_complete.py
uv run python scripts/check_setting_compose_backed.py
uv run python scripts/check_setting_to_startup_trace.py
uv run python scripts/check_no_magic_numbers.py --files <edited files>
uv run python scripts/check_frozen_model_extra_forbid.py --files <edited files>
```

Then the tests covering the consumer. Do not run the full suite here; the
pre-push hook scopes and runs it.

## Phase 7: Review before merging

Post-Implementation Review is MANDATORY: commit, push, and run
`/pre-pr-review`, then `babysit-pr` on the PR until it squash merges. A setting
lands in an operator-facing surface and in the precedence chain at once, which
is exactly the shape a second pass catches.

## Definition of done

- [ ] Precedence category chosen deliberately, Cat-1 unless there is a reason
- [ ] Plan accepted before the definition was registered
- [ ] Definition registered, with a `description` an operator can act on
- [ ] Applies live, or `compose_set` with the env var added to the compose template
- [ ] Consumer reachable from boot, reading through `ConfigResolver`
- [ ] The literal it replaces is gone
- [ ] The five gates above pass
- [ ] `/pre-pr-review` run, and the PR babysat to a squash merge
