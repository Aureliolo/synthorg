---
title: Web Base UI Adoption Decisions
description: Per-primitive adoption decisions for Base UI in the React 19 dashboard, with the rationale for each adopt or reject.
---

# Base UI Adoption Decisions

The dashboard's primitive layer is [Base UI](https://base-ui.com). `components.json` is set to the `base-vega` shadcn style so that any component generated via the shadcn CLI targets Base UI internals, but the adopted primitives below are **imported directly** from `@base-ui/react/*` subpaths (for example `import { Dialog } from '@base-ui/react/dialog'`) with no shadcn wrapper layer in between.

When adding a new primitive, prefer the direct-import path; do not introduce a shadcn wrapper unless there is a concrete reason to diverge.

## Decisions

| Component | Decision | Rationale |
|-----------|----------|-----------|
| `Dialog`, `AlertDialog`, `Popover`, `Tabs`, `Menu` | **Adopted** | Imported directly from `@base-ui/react/*` subpaths across the dashboard's primitive files and page-level dialogs. |
| `CSPProvider` | **Adopted** | Wired in `App.tsx` alongside `MotionConfig` for end-to-end nonce propagation. |
| `merge-props` | **Adopted** | Powers the local `<Slot>` helper in `components/ui/slot.tsx` (preserves the `asChild` ergonomic for `<Button>`). |
| `Drawer` | **Adopted** | Switched from custom Motion-based implementation to Base UI 1.4.0 stable Drawer. Base UI provides focus management (initialFocus / finalFocus), swipe-to-dismiss, modal trap focus, and CSS transitions via `data-[closed]` / `data-[starting-style]` / `data-[ending-style]` selectors (consistent with Dialog, AlertDialog, Popover). Eliminates ~100 lines of hand-rolled a11y code (focus trap, Escape handler, portal). |
| `Toast` | **Not adopted** | Our custom `components/ui/toast.tsx` is a Zustand-backed queue that integrates with the rest of the state stack; Base UI's Toast doesn't couple to external stores. |
| `Meter` | **Not adopted** | `ProgressGauge` already emits `role="meter"` + `aria-valuenow` / `valuemin` / `valuemax`. Base UI's Meter is a raw primitive without the styled circular / linear variants we need. |
| `Select` | **Not adopted** | `SelectField` is a native `<select>`; we intentionally keep the native mobile picker for iOS / Android UX. Replacing with a custom dropdown would lose that. |
| `Combobox`, `Autocomplete` | **Not adopted (for now)** | v1.4.0 adds passive keyboard nav + autofill improvements. No current typeahead call sites in the dashboard (connections page uses button grid, SelectField uses native `<select>`). Re-evaluate when filterable selects become a feature requirement. |
| `OTP Field` | **Not adopted (preview)** | v1.4.0 preview component for one-time password / verification code input. Evaluate when auth / 2FA flows are built (post-v0.7). |

## Adding new primitives

When adding new dashboard primitives, prefer Base UI components for accessibility (Dialog, AlertDialog, Popover, Tabs, Menu, Drawer) and keep the existing custom components (`SelectField`, `Toast`, `ProgressGauge`, animations) where they are; the table above is the canonical rationale.

Tooltip is not yet adopted; reach for an existing primitive first and add a row to the table above if a real Tooltip requirement appears.

## See also

- [web-design-system.md](web-design-system.md): the component inventory plus the per-primitive recipe for composing Base UI subcomponents (`Portal` + `Backdrop` + `Popup`, animation state attributes, Tailwind v4 transition gotchas).
