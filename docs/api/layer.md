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
[`synthorg.core`](core.md):

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

The auth domain types (`AuthConfig`, `User`, `ApiKey`,
`AuthenticatedUser`, `OrgRole`, `HumanRole`, `Session`,
`RefreshRecord`) live under
[`synthorg.core.auth`](core.md#auth); the HTTP-coupled
service, middleware, and request-scoped user binding live in
`synthorg.api.auth`.

`AuthContextMiddleware` (in `synthorg.api.auth.context`) runs
immediately after `ApiAuthMiddleware` and binds the authenticated
user into a per-asyncio-Task `ContextVar`, so controllers and
audit helpers read the user via no-argument accessors
(`get_authenticated_user_id`, `get_authenticated_user`,
`audit_actor_from_context`) without threading a `Request`.

::: synthorg.api.auth.service

::: synthorg.api.auth.middleware

::: synthorg.api.auth.context
