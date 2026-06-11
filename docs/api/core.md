---
search:
  exclude: true
---

# Core

Shared domain models, base types, and enums used across the framework.

## Types

::: synthorg.core.types

## Enums

Domain enums co-locate with the package that owns them. Foundation enums
that core models depend on stay core-local: the task family (status, type,
priority, complexity, stakes, structure, topology, source) plus stakes
ordering in `synthorg.core.task_enums`; project status and the git-backend
/ environment discriminators in `synthorg.core.project_enums`; autonomy
level and ordering in `synthorg.core.autonomy_enums`; tool access level in
`synthorg.core.tool_constraints`; agent memory level and category in
`synthorg.core.memory_enums`; the model completion-outcome (finish reason) in
`synthorg.core.completion_enums`; and artifact type in `synthorg.core.artifact`
(all below). The cross-cutting value objects `EffectiveAutonomy` (resolved
autonomy) and `RedTeamReviewInput` (red-team gate input) also live core-local,
in `synthorg.core.effective_autonomy` and `synthorg.core.redteam_review_input`,
so engine, security, and tools consumers reference them without dragging a heavy
hub at import time. Every other domain enum lives with its owning package: agent
status, the personality traits (risk tolerance, creativity, decision style,
collaboration preference, communication verbosity, conflict approach), cost
tier, seniority, and strategic output mode under [HR](hr.md); company type
and department name under [Organisation](organization.md); the skill-pattern
taxonomy under [Templates](templates.md); memory consolidation interval and
org-fact category under [Memory](memory.md); knowledge source type, content
kind, and source status under [Knowledge](knowledge.md); research source
type, claim type, and run status under [Research](research.md);
living-document type under [Docs Engine](docs_engine.md); the workflow
enums, workspace merge enums (order, escalation, conflict type), operator
intervention kind, and the agent-runtime execution, recovery, and decision
enums under [Engine](engine.md); the conversation turn, status, and proposal
enums under [Communication](communication.md); the charter status enum under
[Meta](meta.md); approval status, risk level, and source in
`synthorg.approval.enums` (below); and the security action-type taxonomy,
tool category, autonomy-downgrade reason, and timeout-action type under
[Security](security.md).

## Task Enums

::: synthorg.core.task_enums

## Project Enums

::: synthorg.core.project_enums

## Autonomy Enums

::: synthorg.core.autonomy_enums

## Tool Constraints

::: synthorg.core.tool_constraints

## Memory Enums

::: synthorg.core.memory_enums

## Completion Enums

::: synthorg.core.completion_enums

## Agent

::: synthorg.core.agent

## Company

::: synthorg.core.company

## Role

::: synthorg.core.role

## Role Catalog

::: synthorg.core.role_catalog

## Task

::: synthorg.core.task

## Task Transitions

::: synthorg.core.task_transitions

## Project

::: synthorg.core.project

## Approval

::: synthorg.approval.enums

::: synthorg.core.approval

## Effective Autonomy

::: synthorg.core.effective_autonomy

## Red-Team Review Input

::: synthorg.core.redteam_review_input

## Artifact

::: synthorg.core.artifact

## Personality

::: synthorg.core.personality

## Resilience Config

::: synthorg.core.resilience_config

## Auth {#auth}

The auth domain types live in ``synthorg.core.auth`` (not ``synthorg.api.auth``) so persistence repositories and engine modules can reference user / session / refresh-record / role models without crossing a layer boundary into the HTTP-coupled API package. The ``AuthService``, controllers, and middleware that bind to Litestar / JWT issuer-audience constants stay under ``synthorg.api.auth``.

::: synthorg.core.auth.config

::: synthorg.core.auth.models

::: synthorg.core.auth.session

::: synthorg.core.auth.refresh_record

::: synthorg.core.auth.roles
