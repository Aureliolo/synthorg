---
title: UX Research & Framework Decision
description: Research, evaluation, and rationale behind the Vue 3 to React 19 migration for the SynthOrg web dashboard.
---

# UX Research & Framework Decision

This document records the framework evaluation and migration decision that shaped the web dashboard rebuild.

## Background

The initial web dashboard (v0.1.3, #347) was built with **Vue 3 + PrimeVue + Pinia + ECharts + VueFlow**. While functional, the UX audit (#762) identified severe problems across every page: static data presentation, missing interactivity, no visual hierarchy, inconsistent components, and lack of polish. The setup wizard was the only well-designed flow.

Rather than incrementally fixing each page within the Vue stack, the team evaluated whether a framework migration would better serve the project's goals, particularly around component ownership, keyboard-first interaction, animation richness, and AI-assisted development.

## Framework Evaluation

| Criterion | Vue 3 + PrimeVue | React 19 + shadcn/ui | Svelte 5 | HTMX |
|-----------|------------------|----------------------|----------|------|
| **Component ownership** | npm dependency (PrimeVue owns components, updates can break) | Copy-paste model (shadcn generates into your codebase, full control) | Own components but smaller ecosystem | Server-rendered, minimal client components |
| **Keyboard-first UX** | No established solution | cmdk-base (maintained cmdk port on Base UI Dialog) | No established solution | Not applicable |
| **Animation ecosystem** | Limited (Vue Transition, no physics-based library at PrimeVue's level) | Motion (spring physics, layout animations, gesture support) | Built-in transitions but limited physics | Not applicable |
| **Accessibility primitives** | PrimeVue has ARIA support | Base UI (headless, fully accessible, composable) | Limited headless options | Server-rendered (inherently accessible) |
| **TypeScript DX** | Good but JSX errors less descriptive | Better TS error messages, especially for AI-assisted development | Good | Minimal TS involvement |
| **State management** | Pinia (Vue-specific) | Zustand (framework-agnostic, minimal API surface) | Runes (built-in) | Server state |
| **Ecosystem maturity** | Large but smaller than React | Largest ecosystem, most third-party libraries | Growing rapidly | Niche |
| **Visualization libraries** | ECharts, VueFlow | Recharts, @xyflow/react | D3-based options | Server-rendered charts |

## Decision

**React 19 + shadcn/ui + Zustand** was chosen for the dashboard rebuild (#762).

The deciding factors were:

1. **Component ownership**: shadcn/ui's copy-paste model means SynthOrg owns every component. No upstream dependency can break the UI on update. Components are customised in-place rather than fighting a library's opinion.

2. **Keyboard-first interaction**: cmdk-base (the maintained cmdk port on Base UI Dialog) provides a production-ready command palette. This is central to SynthOrg's interaction model: operators manage autonomous agents and need fast, keyboard-driven access to any action.

3. **Animation language**: Motion enables the "Warm Ops" design identity: spring-based entrance animations, layout transitions, and gesture interactions that make an autonomous operations dashboard feel alive rather than static.

4. **Accessibility**: Base UI primitives handle WAI-ARIA compliance at the component level. Combined with shadcn/ui's composable approach, accessibility is built-in rather than bolted-on.

5. **AI-assisted development**: React's TypeScript integration produces more descriptive error messages, which improves the quality of AI-generated code contributions, relevant given SynthOrg's development workflow.

See also: [Tech Stack decisions table](../architecture/tech-stack.md) (Web UI row).

## Migration Timeline

The migration was executed as part of the UX overhaul (#762):

| Phase | Issues | Scope |
|-------|--------|-------|
| 0. Scaffold | #768 | React 19 + Vite 8 + TypeScript project setup |
| 1. Infrastructure | #769 | Routing, state management, API client, WebSocket, auth |
| 2. App shell | #770 | Layout, sidebar, status bar, command palette |
| 3. API endpoints | #771--#774 | Backend API extensions for dashboard data needs |
| 4. Design system | #775 | Design tokens, shared components, Storybook |
| 5. Pages | #777--#789 | Individual page implementations |
| 6. Cross-cutting | #790--#793 | Real-time UX, responsive, accessibility, performance QA |
| 7. Cleanup | #794 | Remove Vue remnants, update infrastructure, close issues |

## Outcome

- Zero Vue, PrimeVue, or Pinia code remains in the `web/` directory
- All Docker, CI, and documentation infrastructure references React 19
- The Vue-era commit history is preserved in the changelog for audit purposes
- Design system documentation lives in [Brand Identity & UX](brand-and-ux.md), [UX Guidelines](ux-guidelines.md), and [Page Structure](page-structure.md)
