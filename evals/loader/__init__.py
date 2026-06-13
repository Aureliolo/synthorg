"""YAML loaders for the eval spine.

Each loader is the file-boundary ingress for one data shape: briefs,
company configs, judge anchors. Loaders route through
:func:`synthorg.core.boundary.parse_typed` so validation failures emit
the same structured log event used by every other typed boundary.
"""
