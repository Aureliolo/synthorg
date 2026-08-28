---
title: Dashboard Framework Research
description: The framework evaluation behind the dashboard stack, the alternatives weighed against it, and what each library is responsible for.
---

# Dashboard Framework Research

This page records why the dashboard is built on the stack it is built on. It is
a framework evaluation, not user research: no operator study, interview round or
usability test underlies it, and no page should cite it as though one did.

## The Stack

| Layer | Choice | Responsible for |
|-------|--------|-----------------|
| Framework | React 19 | Rendering and component model |
| Components | shadcn/ui, vendored into `web/src/components/ui/` | Every shared primitive the dashboard composes pages from |
| Accessibility primitives | Base UI | Dialog, popover, checkbox and the other headless behaviours the primitives wrap |
| State | Zustand | Client stores fed by REST reads and WebSocket events |
| Command palette | `cmdk-base` (the cmdk port on Base UI Dialog) | Global Cmd+K search and page-local scopes |
| Animation | Motion | Spring and tween transitions, layout animation, reduced-motion detection |
| Charts | Recharts | Sparklines, trends, forecast, and budget charts |
| Graph canvases | `@xyflow/react`, `@dagrejs/dagre`, `d3-force` | Workflow editor, org-chart hierarchy layout, communication view |
| Drag and drop | `@dnd-kit` | Agent reassignment, team reordering |
| Icons | `lucide-react` | Every icon in the dashboard |
| Build | Vite, TypeScript | Development server, bundling, type checking |

Data fetching is deliberately not on that list. The dashboard has no query
library: reads go through the typed API client in `web/src/api/`, and anything
that needs to refresh without a WebSocket channel uses the project's own
`usePolling` hook, which carries a `skipIfFresh` gate so a page already fed by a
live channel does not pay for the same state twice.

## Alternatives Considered

| Criterion | React + shadcn/ui | Vue + PrimeVue | Svelte | HTMX |
|-----------|-------------------|----------------|--------|------|
| **Component ownership** | Copy-paste model: the components live in this repository and are edited in place | npm dependency; the library owns the components and an update can change them | Own components, smaller ecosystem to draw them from | Server-rendered, minimal client components |
| **Keyboard-first UX** | `cmdk-base`, a maintained command-palette primitive | No established solution | No established solution | Not applicable |
| **Animation** | Motion: spring physics, layout animations, gesture support | Vue Transition, with no physics-based library at the same level | Built-in transitions, limited physics | Not applicable |
| **Accessibility primitives** | Base UI: headless, composable, WAI-ARIA behaviours | ARIA support inside the component library | Fewer headless options | Server-rendered |
| **TypeScript experience** | Descriptive errors, which matters for machine-written code | Good, with less descriptive JSX errors | Good | Minimal TypeScript involvement |
| **State management** | Zustand: framework-agnostic, small API surface | Pinia, Vue-specific | Runes, built in | Server state |
| **Ecosystem** | Largest, so a niche need usually has a maintained answer | Large, smaller than React's | Growing | Niche |
| **Visualisation** | Recharts, `@xyflow/react` | ECharts, VueFlow | D3-based options | Server-rendered charts |

## Why This One

**Component ownership decided it.** The copy-paste model means the dashboard
owns every component: no upstream release can change the UI, and a primitive is
customised in place rather than fought with. That matters more here than in a
typical application, because the design system is enforced by a gate
(`scripts/check_web_design_system.py`) that can only enforce rules over
components the repository actually holds.

**Keyboard-first interaction is not decoration.** The dashboard is an operator
surface for a system that is supervised, not autonomous, so the operator is in
the loop constantly and needs fast access to any page, agent, or setting. A
maintained command-palette primitive was a real differentiator between the
options.

**Accessibility is composed in, not bolted on.** Base UI supplies the behaviours
(focus trapping, dialog semantics, roving focus) and the vendored primitives
supply the styling, so an accessible dialog is the default rather than an
achievement. The Storybook accessibility add-on fails a story on a WCAG
violation, which only works because the components are local.

**Machine-written code is a first-class consumer.** Much of this dashboard is
written by agents, and TypeScript diagnostics are part of the feedback loop they
work against. More descriptive errors mean fewer wasted rounds.

See also the [Tech Stack decisions table](../architecture/tech-stack.md).

## Reference Materials

| Resource | Location |
|----------|----------|
| Brand identity, voice rules, and visual rationale | [Brand & UX](brand-and-ux.md) |
| Implementation specifications | [UX Guidelines](ux-guidelines.md) |
| Page structure and navigation | [Page Structure & IA](page-structure.md) |
| Dependency manifest | `web/package.json` |
