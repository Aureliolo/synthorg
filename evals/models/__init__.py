"""Frozen Pydantic models for the eval spine.

Every model exposed here uses ``ConfigDict(frozen=True, extra="forbid")``
so YAML payloads, scorecard documents, and cassette manifests round-trip
deterministically and cannot accumulate undeclared fields.
"""
