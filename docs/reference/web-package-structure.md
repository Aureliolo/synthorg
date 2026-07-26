---
title: Web Package Structure
description: Directory layout for web/src/ (React 19 dashboard) and web/e2e/ (Playwright fixtures, factories, flows).
---

# Web Package Structure

On-demand reference for the React dashboard's directory layout. The short summary in `web/CLAUDE.md` lists the top-level folders; this page is the per-folder inventory that explains what lives where and why.

## `web/src/`

```text
web/src/
  api/            # Axios client (`client.ts`), endpoint modules (`endpoints/`, 67 modules), and narrow-domain types under `types/` (51 hand-written modules; there is no barrel `index.ts` and consumers import from `@/api/types/<domain>`, one barrel per name)
  components/     # React components: ui/ (shadcn primitives + SynthOrg core components), layout/ (app shell, sidebar with external link support, status bar); feature dirs added as pages are built
  hooks/          # React hooks (auth, login lockout, WebSocket, polling, freshness gate, optimistic updates, command palette, flash effects, status transitions, page data composition, count animation, auto-scroll, roving tabindex, breakpoint detection, update tracking, animation presets, settings dirty state, settings keyboard shortcuts, communication edges, artifact / project data composition, useWorkflowsData, useBulkSelection, useEmptyStateProps)
  lib/            # Utilities (cn() class merging, semantic color mappers), Motion presets, CSP nonce reader, structured logger factory
  mocks/          # MSW request handlers (handlers/) shared between Storybook stories and the Vitest suite; test-setup.tsx bootstraps them via setupServer(...defaultHandlers)
  pages/          # Lazy-loaded page components (one per route); page-scoped sub-components in pages/<page-name>/ subdirs (e.g. tasks/, org-edit/, settings/, workflows/, fine-tuning/, training/, security/)
  router/         # React Router config, route constants (incl. DOCUMENTATION, an external link not SPA-routed), auth/setup guards
  stores/         # Zustand stores (auth, WebSocket, toast, analytics, company, agents, approvals, budget, backups, collaboration, meetings, messages, tasks, settings, sinks, artifacts, projects, theme, workflows, fine-tuning, ceremony-policy, setup, training, promotion, ssrf-violations, per-domain stores). See "Store slicing patterns" below.
  styles/         # Design tokens (--so-* CSS custom properties, single source of truth), typed status-colour lookups (status-colors.ts: ROLE_BADGE_COLORS, ESCALATION_STATUS_BADGE_COLORS), and Tailwind theme bridge
  utils/          # Constants, error handling, formatting, logging
  __tests__/      # Vitest unit + property tests (mirrors src/ structure)
```

### One barrel per name

A generated DTO or enum is reachable from exactly one module: `@/api/types/<domain>`. `api/types/<x>.ts` mirrors `api/endpoints/<x>.ts`, except where an existing module already owns the concept (`UserResponse` sits in `auth.ts`, `WebhookReceipt` and `InstalledEntry` in `integrations.ts`).

Three shapes are banned because each re-creates a second path to the same name, and a name reachable two ways can never be reported unused:

- No `api/types/index.ts`, and no `export *` from a `.gen` module. Both hide drift instead of surfacing it: list the names the dashboard imports and add a new one in the same commit as its consumer.
- No DTO pass-through in an endpoint module. Those export behaviour plus the types they derive (`clients.ts`'s `StageVerdict`, `SimulationReport`); DTO shapes come from the type barrel.
- No convenience alias re-exporting a name that already has a home elsewhere.

This is what keeps knip's `types` report armed and honest, so a re-export nothing consumes fails the build rather than accumulating. Enforced by `no-restricted-imports` plus two `no-restricted-syntax` rules in `web/eslint.config.js`.

The hand-written modules that are not DTO groupings keep their own paths: `http.ts` (the `ApiResponse<T>` / `PaginatedResponse<T>` envelopes), `errors.ts` (the `ErrorCode` taxonomy), `enums.ts` (generated enum tuples plus type guards) and the `websocket/` sub-package.

### Store slicing patterns

Stores over ~600 lines are sliced into packages. Two aggregation patterns are used:

1. **Package-internal index.** `setup-wizard/` (navigation, template, company, providers, agents, theme, completion) and `workflow-editor/` (graph, undo-redo, validation, clipboard, persistence, versions, yaml) both expose a composed `index.ts`; consumers import from `@/stores/setup-wizard` / `@/stores/workflow-editor`.
2. **Sibling aggregator module.** `providers/` (crud-actions, list-actions, local-model-actions), `connections/` (crud-actions, list-actions), `mcp-catalog/` (list-actions, install-actions) each live next to a top-level `providers.ts` / `connections.ts` / `mcp-catalog.ts` which composes the slices; consumers import from `@/stores/providers` etc. which resolves to the `.ts` aggregator.

Each package has a `types.ts` regardless of pattern.

## `web/e2e/`

```text
web/e2e/
  factories/      # Typed mock-data builders mirroring API response shapes:
                  # agents, approvals, budget, memory, providers, setup,
                  # tasks, workflows. Each factory accepts an overrides
                  # object so tests vary single fields without rebuilding
                  # the whole payload (#1604 / W5a).
  fixtures/       # mock-api.ts (route stubs + freezeTime) and
                  # websocket-harness.ts (#1604 / W5a). The harness
                  # swaps the global WebSocket constructor for a
                  # controllable stub via page.addInitScript and exposes
                  # `injectEvent(page, event)` so flow specs can push
                  # synthetic server-pushed frames (task transitions,
                  # approval decisions, provider health) through the
                  # same handler the SPA processes for real connections.
  flows/          # Playwright flow specs. Every Tier-1 spec installs
                  # the WebSocket harness, builds deterministic API
                  # responses through the factories, and asserts both
                  # form interaction AND WS-driven state transitions.
                  # task-lifecycle.spec.ts walks the full create -> approval
                  # gate -> reviewer-approves -> status transition path.
  helpers/        # interactions.ts: dragTo / fillForm / clickButton /
                  # clickAndAwait / selectOption wrappers that always
                  # wait on a selector or network response, never on a
                  # wall-clock timeout.
  visual/         # Playwright visual-regression specs.
```

## See also

- [web-design-system.md](web-design-system.md): the `components/ui/` inventory.
- [web-zustand-stores.md](web-zustand-stores.md): the `stores/` mutation pattern, MSW handler contract, active-handle gate.
- [web-base-ui-decisions.md](web-base-ui-decisions.md): which Base UI primitives the dashboard uses.
- [web-post-training.md](web-post-training.md): TypeScript 6 / Storybook 10 facts.
