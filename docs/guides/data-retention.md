---
title: Data Retention & GDPR
description: Retention windows and deletion paths for personally identifiable information (PII) across SynthOrg's persistence layer.
---

# Data Retention & GDPR

SynthOrg stores a small set of PII fields to support authentication,
audit, and work-tracking. This page lists every PII field, where it
lives, how long it is retained, and how it is removed.

## Summary

| PII field | Table(s) | Retention | Deletion trigger |
|-----------|----------|-----------|------------------|
| `users.username`, `.password_hash`, `.org_roles`, `.scoped_departments` | `users` | Indefinite while account is active | `DELETE /api/v1/users/{id}` |
| `sessions.ip_address`, `.user_agent` | `sessions` | Until `expires_at` | Ticket cleanup loop (every 60s) + FK cascade on user delete |
| `api_keys.key_hash` | `api_keys` | Until revoked or `expires_at` | FK cascade on user delete + scheduled cleanup |
| `login_attempts.ip_address` | `login_attempts` | 15 minutes (default lockout window) | Lockout cleanup loop |
| `audit_entries.agent_id`, `.arguments_hash` | `audit_entries` | `security.audit_retention_days` (default 730 = 2 years) | Audit retention loop (daily) |
| Refresh token hashes | `refresh_tokens` | Until revoked or `expires_at` | `UserService.delete()` explicit revocation + FK cascade on user delete + periodic sweep of expired rows |
| `tasks.created_by`, `artifacts.created_by` | respective tables | Indefinite; authorship preserved | Not cascaded on user delete (content outlives its author) |
| Client profile / feedback / requests | `client_*` tables | Indefinite; authorship preserved | Not cascaded on user delete |
| Agent memory entries (`MemoryBackend`) | backend-specific (Mem0 / Qdrant) | Indefinite while agent is active | `DELETE /api/v1/admin/memory/agents/{agent_id}/memories/{memory_id}` and the `synthorg_memory_delete_entry` MCP tool |
| Channel messages (`Message.sender`, `.metadata`, `.channel`) | `messages` | Indefinite; operator-driven removal | `DELETE /api/v1/messages/{message_id}` and the `synthorg_messages_delete` MCP tool |
| Meeting records (`MeetingRecord.minutes.contributions[*].agent_id`) | in-memory orchestrator audit store | Per-process; flushed on restart unless persisted | `DELETE /api/v1/meetings/{meeting_id}` and the `synthorg_meetings_delete` MCP tool |

## Cascade behavior on user deletion

When an operator issues `DELETE /api/v1/users/{user_id}` the server:

1. **Revokes refresh tokens (defense-in-depth)**: the
   `UserService.delete()` method calls
   `RefreshTokenRepository.revoke_by_user(user_id)` before the DB
   delete so outstanding tokens cannot continue to mint access
   tokens even while the delete is in flight or retried. If the
   revocation raises, the user delete is aborted (fail-closed) so
   tokens are never left live alongside a deleted user.
2. **Cascades via foreign key**: the `api_keys`, `sessions`, and
   `refresh_tokens` tables all declare
   `user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE`,
   so API keys, active sessions (including their recorded IP/UA),
   and any remaining refresh token rows are removed atomically
   when the user row is deleted.
3. **Preserves audit entries**: `audit_entries.agent_id` carries
   the agent identifier, not the user id; there is no FK from audit
   entries to users. Audit integrity is intentionally held above
   PII erasure (see `docs/design/security.md`).
4. **Preserves authorship records**: `tasks.created_by` and
   `artifacts.created_by` are not cascaded. Work outlives its
   author; removing the author would destroy project history.

The cascade set is covered by integration tests in
`tests/integration/api/test_user_deletion_cascade.py`, which
exercise `UserService.delete()` end-to-end against the SQLite
backend and assert the refresh-token revocation + FK cascade
behaviour.

## Audit retention window

The `audit_entries` table has an operator-configurable retention
window controlled by two settings:

| Setting | Default | Effect |
|---------|---------|--------|
| `security/audit_retention_days` | `730` (2 years) | Rows older than this are purged daily. Set to `0` to disable purging. |
| `security/retention_cleanup_paused` | `false` | When `true`, pauses the daily sweep without tearing down the lifecycle task. Use during incident investigations. |

The background loop `_audit_retention_loop` lives in
`src/synthorg/api/lifecycle_helpers.py` and runs once every 24
hours. Each tick:

1. Reads the two settings above.
2. Skips the tick if paused or if `retention_days <= 0`.
3. Computes `cutoff = utcnow() - retention_days * 86400`.
4. Calls `AuditRepository.purge_before(cutoff)` which issues
   `DELETE FROM audit_entries WHERE timestamp < cutoff`.
5. Logs the result under the `API_APP_STARTUP` event with
   `note="audit retention purge completed"` plus the deleted row
   count, configured retention window, and cutoff timestamp.

Both SQLite and Postgres backends implement
`purge_before` (see `src/synthorg/persistence/sqlite/audit_repository.py`
and `.../postgres/audit_repository.py`).

## Session history cleanup

Expired sessions (including their IP / UA fields) are removed every
60 seconds by `_ticket_cleanup_loop`. The cadence is
operator-tunable via `api/ticket_cleanup_interval_seconds`. The
cleanup uses `DELETE FROM sessions WHERE expires_at <= ?` which
drops the whole row so historical IP/UA data does not linger past
the session TTL.

## Login-attempt retention

Login-attempt rows drive the account-lockout policy (five failures
in 15 minutes by default). Expired rows are cleared by the same
cleanup loop; look for `API_AUTH_LOCKOUT_CLEANUP` in the logs.

## Out of scope

- **Right-to-be-forgotten on audit records.** Audit is append-only
  by design. Operators who need to remove a specific audit entry
  (e.g. to comply with a targeted GDPR erasure request) must issue
  a scoped DELETE themselves and document the justification. The
  `purge_before(cutoff)` method only supports time-range removal.
- **Export / takeout.** A dedicated "download all my data"
  endpoint is not provided today; operators export via SQL.

## See also

- [Settings Reference](settings-reference.md): full catalog of
  runtime-editable settings, including the retention knobs.
- [Security design](../design/security.md): the rationale behind
  audit append-only + preserved-authorship tradeoffs.
