"""Department controllers.

Split per ADR-0008 into per-concern controller sub-modules that share
the ``/departments`` path: :mod:`crud` (listing + mutations),
:mod:`health` (health aggregation), and :mod:`ceremony_policy` (policy
overrides). Cross-cutting ceremony-policy store mechanics live in
:mod:`_shared`. Import controllers directly from their sub-module.
"""
