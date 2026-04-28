---
search:
  exclude: true
---

# API Layer

Litestar REST + WebSocket API: controllers, authentication, guards, and channels.

## App

::: synthorg.api.app

## Config

::: synthorg.api.config

## DTOs

::: synthorg.api.dto

## Errors

The error taxonomy and exception classes live in
[`synthorg.core`](../core/index.md):

- `synthorg.core.error_taxonomy` -- `ErrorCategory`, `ErrorCode`,
  RFC 9457 helpers
- `synthorg.core.domain_errors` -- `DomainError` base + concrete
  subclasses (`NotFoundError`, `ConflictError`, `ValidationError`, ...)
- `synthorg.core.persistence_errors` -- `PersistenceError` hierarchy

## Guards

::: synthorg.api.guards

## Middleware

::: synthorg.api.middleware

## Pagination

::: synthorg.api.pagination

## WebSocket Models

::: synthorg.api.ws_models

## Auth

::: synthorg.api.auth.config

::: synthorg.api.auth.models

::: synthorg.api.auth.service

::: synthorg.api.auth.middleware
