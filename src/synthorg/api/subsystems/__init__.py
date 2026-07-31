# module-kind: declarative
"""Declarative subsystem model and its reconciler.

Deliberately re-exports nothing: the reconciler imports the registry, which
imports every wiring module, so a hub ``__init__`` would drag the whole boot
graph into any cold import of :mod:`synthorg.api`.
"""
