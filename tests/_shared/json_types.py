"""Shared type aliases for JSON-shaped test data.

Many tests build and deep-index arbitrary JSON documents (API request
and response bodies, OpenAPI schema trees, MCP envelopes, webhook
payloads). Precise typing of these dynamically-shaped, deeply-indexed
trees is impractical, so the explicit ``Any`` is confined to a single
named alias here rather than scattered across every test module.

Use ``JsonDict`` only for genuinely heterogeneous, dynamically-indexed
JSON objects. Prefer precise types (or :class:`pydantic.JsonValue`)
wherever the shape is known.
"""

from typing import Any

type JsonDict = dict[str, Any]  # type: ignore[explicit-any]  # confined alias for dynamically-indexed JSON; values are irreducibly arbitrary
"""A dynamically-shaped JSON object; values are arbitrary."""

type AsgiDict = dict[str, Any]  # type: ignore[explicit-any]  # confined alias for hand-rolled ASGI scope/message stubs; values are irreducibly arbitrary
"""A loosely-typed ASGI scope or message dict for hand-rolled test stubs.

The real :class:`litestar.types.Scope` / ``Message`` are strict TypedDict
unions; test stubs build partial scopes and index them dynamically, so a
loose dict is the honest, churn-free type here.
"""
