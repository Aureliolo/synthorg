---
search:
  exclude: true
---

# Core

Shared domain models, base types, and enums used across the framework.

## Types

::: synthorg.core.types

## Enums

::: synthorg.core.enums

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
