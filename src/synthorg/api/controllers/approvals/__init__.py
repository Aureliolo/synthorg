"""Human approval-queue controllers, split by read vs decision concerns.

The approvals surface is two controllers sharing ``/approvals`` --
``query`` (list + get, read-only) and ``decisions`` (create, approve,
reject) -- backed by two helper modules: ``_shared`` (urgency-threshold
resolution, the urgency-enriched response DTO + conversion, and the
fetch-or-404 helper used by both) and ``_notify`` (the decision-path
actor attribution, pending-state validation, WebSocket publishing, and
persist-decide-notify sequence).

Direct imports only:
``from synthorg.api.controllers.approvals.<module> import ...``.
This package's ``__init__`` deliberately stays empty so each
sub-controller and helper module is referenced at its own import site.
"""
