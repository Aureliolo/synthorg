"""Framework-internal substrate for SynthOrg.

``_core`` holds cross-cutting substrate that the rest of the package
composes against: the feature-manifest model, the typed state-slice
base, and feature discovery. It is deliberately distinct from the
domain-level :mod:`synthorg.core` package (which owns clocks, enums,
errors, and value-object types). The leading underscore marks it as
internal plumbing rather than a domain area.
"""
