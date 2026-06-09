"""User management controllers.

Split per ADR-0008 into per-concern controller sub-modules sharing the
``/users`` path: :mod:`account` (CEO-only user CRUD) and
:mod:`org_roles` (org-level role grant/revoke). The shared service
factory, public response DTO, and lookup helper live in :mod:`_shared`.
Import controllers directly from their sub-module.
"""
