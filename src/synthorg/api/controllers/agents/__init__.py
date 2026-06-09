"""Agent controllers, split into CRUD + observability concerns.

Two controllers share the ``/agents`` path: ``crud``
(``AgentCrudController``: list, get, create, update, delete) and
``observability`` (``AgentObservabilityController``: performance,
activity, history, health). Both resolve an agent from the path id via
the ``_shared`` helpers (config-resolver lookup for CRUD, live-registry
lookup for observability).

Direct imports only:
``from synthorg.api.controllers.agents.crud import AgentCrudController``.
This package's ``__init__`` deliberately stays empty so each controller
and helper is referenced at its own import site.
"""
