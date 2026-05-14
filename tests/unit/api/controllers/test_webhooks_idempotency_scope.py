"""Regression coverage for webhook idempotency scope + key invariants.

The webhook flow uses the durable ``IdempotencyService`` under a scope
of the form
``webhooks:{len(connection_type)}:{connection_type}:{len(connection_name)}:{connection_name}``
and a key of the form
``{len(connection_name)}:{connection_name}:{len(event_type)}:{event_type}:{len(nonce_for_key)}:{nonce_for_key}``.
Length-prefixing every segment makes the encoding injective: distinct
``(connection_type, connection_name)`` (or ``(connection_name,
event_type, nonce)``) tuples can never produce the same composite
string, even when one of the parts contains a literal ``":"``. Pinning
both fields prevents two distinct connections of the same provider
from colliding on a shared dedup row.
"""

import pytest

from synthorg.api.controllers.webhooks import _build_idem_key, _build_idem_scope

pytestmark = pytest.mark.unit


class TestWebhookIdempotencyKey:
    def test_key_contains_connection_name(self) -> None:
        key = _build_idem_key(
            connection_name="example-provider-primary",
            event_type="push",
            nonce="nonce-abc",
        )
        assert "example-provider-primary" in key
        assert "push" in key
        assert "nonce-abc" in key

    def test_distinct_connections_produce_distinct_keys(self) -> None:
        first = _build_idem_key(
            connection_name="example-provider-primary",
            event_type="push",
            nonce="nonce-abc",
        )
        second = _build_idem_key(
            connection_name="example-provider-secondary",
            event_type="push",
            nonce="nonce-abc",
        )
        assert first != second


class TestWebhookIdempotencyScope:
    """Scope MUST include ``connection_name`` to block cross-connection collisions.

    Pins the live string format the controller assembles. If the format
    changes (e.g. drops ``connection_name`` or reorders the tokens),
    this invariant test fails and forces a deliberate review.
    """

    def test_scope_format_includes_connection_name(self) -> None:
        # Exercise the controller's own helper so a future refactor
        # that drops ``connection_name`` from the scope format trips
        # the assertion. A local f-string would silently keep passing.
        connection_type = "example-provider"
        connection_name = "example-provider-primary"
        scope = _build_idem_scope(
            connection_type=connection_type,
            connection_name=connection_name,
        )
        # Exact-equality pin: any reordering of the token positions
        # (e.g. swapping connection_type with connection_name) trips
        # the assertion immediately. Substring / startswith checks
        # would silently keep passing under such a refactor. The
        # length-prefix on every segment is what makes the encoding
        # injective: two different ``(type, name)`` tuples cannot
        # collapse to the same string even when one part contains
        # a literal ``":"``.
        assert scope == (
            f"webhooks:{len(connection_type)}:{connection_type}"
            f":{len(connection_name)}:{connection_name}"
        )
        # And scopes for sibling connections of the same type must differ.
        sibling = _build_idem_scope(
            connection_type=connection_type,
            connection_name="example-provider-secondary",
        )
        assert scope != sibling
