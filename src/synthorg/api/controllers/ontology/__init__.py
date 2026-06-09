"""Ontology REST API controllers.

Split per ADR-0008 into per-concern controller sub-modules sharing the
``/ontology`` path: :mod:`entities` (entity-definition CRUD),
:mod:`versions` (version history + manifest), :mod:`drift` (drift
detection), and :mod:`admin` (derivation + org-memory sync). The shared
service factory, entity serializer, and default page size live in
:mod:`_shared`. Import controllers directly from their sub-module.
"""
