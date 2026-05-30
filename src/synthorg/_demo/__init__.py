"""Synthetic demo feature: the discovery regression guard.

A deliberately trivial end-to-end feature (service + state slice + construction
wirer + REST controller + MCP tool + setting) that exercises every limb of the
feature-manifest substrate. It must stay reachable with ZERO edits to
``api/app.py`` / ``api/state.py`` / any central wiring, proving a new feature
touches only its own directory. See ADR-0008.
"""
