---
search:
  exclude: true
---

# Persistence

Pluggable operational data persistence: protocol, configuration, SQLite backend, and Postgres backend.

## Protocol

::: synthorg.persistence.protocol

## Config

::: synthorg.persistence.config

## Repositories

Repository protocols are split by domain so each lives alongside its
typed-ID and helper imports.

::: synthorg.persistence.agent_state_protocol

::: synthorg.persistence.artifact_protocol

::: synthorg.persistence.audit_protocol

::: synthorg.persistence.checkpoint_protocol

::: synthorg.persistence.connection_protocol

::: synthorg.persistence.cost_record_protocol

::: synthorg.persistence.decision_protocol

::: synthorg.persistence.message_protocol

::: synthorg.persistence.parked_context_protocol

::: synthorg.persistence.project_protocol

::: synthorg.persistence.settings_protocol

::: synthorg.persistence.task_protocol

::: synthorg.persistence.user_protocol

## Factory

::: synthorg.persistence.factory

## Errors

::: synthorg.core.persistence_errors

## SQLite Backend

::: synthorg.persistence.sqlite.backend

::: synthorg.persistence.sqlite.repositories

## Postgres Backend

::: synthorg.persistence.postgres.backend

::: synthorg.persistence.postgres.repositories
