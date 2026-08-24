"""Department controllers.

Split per ADR-0008 into per-concern controller sub-modules that share
the ``/departments`` path: :mod:`crud` (listing + mutations) and
:mod:`health` (health aggregation). Import controllers directly from
their sub-module.
"""
