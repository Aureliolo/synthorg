"""Periodic lifecycle helpers, split by concern.

Direct imports only:

- :mod:`synthorg.api.lifecycle_helpers.ticket_cleanup`: WS ticket /
  session / lockout / OAuth-state / idempotency cleanup loop, plus
  event-stream janitor cadence resolution.
- :mod:`synthorg.api.lifecycle_helpers.audit_retention`: daily audit-
  table retention purge.
- :mod:`synthorg.api.lifecycle_helpers.bootstrap`: one-time owner
  promotion and agent bootstrap on app startup.
- :mod:`synthorg.api.lifecycle_helpers.settings_dispatcher`:
  ``SettingsChangeDispatcher`` assembly.
- :mod:`synthorg.api.lifecycle_helpers.config_apply`: operator-tuned
  bridge-config snapshotting and per-setting application.

This package's ``__init__`` deliberately stays empty so the boundary
between cleanup loops, retention sweeps, bootstrap, dispatcher wiring,
and config-apply is explicit at every call site.
"""
