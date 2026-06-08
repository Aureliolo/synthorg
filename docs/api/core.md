---
search:
  exclude: true
---

# Core

Shared domain models, base types, and enums used across the framework.

## Types

::: synthorg.core.types

## Enums

`synthorg.core.enums` holds the remaining cross-cutting domain enums.
Foundation enums co-locate with the core model they describe: the task
family (status, type, priority, complexity, stakes, structure, topology,
source) plus stakes ordering in `synthorg.core.task_enums`; project
status and the git-backend / environment discriminators in
`synthorg.core.project_enums`; autonomy level and ordering in
`synthorg.core.autonomy_enums`; tool access level in
`synthorg.core.tool_constraints`; and artifact type in
`synthorg.core.artifact` (below). Many other domain-specific enums live
with their owning package: agent memory level and category in
`synthorg.core.memory_enums` (below); seniority and strategic output mode
under [HR](hr.md); memory consolidation interval and org-fact category
under [Memory](memory.md); knowledge source type, content kind, and
source status under [Knowledge](knowledge.md); research source type,
claim type, and run status under [Research](research.md); living-document
type under [Docs Engine](docs_engine.md); the workflow type, node,
edge, value, and status enums under [Engine](engine.md); approval status,
risk level, and source in `synthorg.approval.enums` (below); and the
security action-type taxonomy, tool category, autonomy-downgrade reason,
and timeout-action type under [Security](security.md).

::: synthorg.core.enums

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
