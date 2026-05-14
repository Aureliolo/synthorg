"""Webhook controller routes through the service layer and typed boundary.

Two structural invariants are pinned:

1. ``receive_webhook`` parses the untrusted body through a typed
   :class:`WebhookEventPayload` boundary that rejects non-JSON bodies and
   non-object payloads (arrays, scalars, ``null``).
2. ``list_activity`` never reaches into ``state["app_state"].persistence``;
   it routes through :class:`WebhookActivityService` instead.

A static AST walk verifies the controller never touches
``persistence.webhook_receipts``. Parametrised model tests verify the
Pydantic boundary rejects non-object payloads.
"""

import ast
import inspect
from typing import Any

import pytest
from pydantic import ValidationError

from synthorg.api.controllers import webhooks as webhooks_module
from synthorg.api.controllers.webhooks import (
    WebhookEventPayload,
    WebhooksController,
    _get_activity_service,
)
from synthorg.integrations.webhooks.activity_service import (
    WebhookActivityService,
)

pytestmark = pytest.mark.unit


class TestWebhookEventPayload:
    """The typed boundary for inbound webhook payloads."""

    def test_accepts_provider_specific_keys(self) -> None:
        """``extra='allow'`` lets through arbitrary external-provider keys."""
        payload = WebhookEventPayload.model_validate(
            {"action": "opened", "issue": {"number": 42}, "sender": "x"},
        )
        assert payload.model_dump() == {
            "action": "opened",
            "issue": {"number": 42},
            "sender": "x",
        }

    def test_frozen(self) -> None:
        """Frozen ConfigDict prevents post-construction mutation."""
        payload = WebhookEventPayload.model_validate({"x": 1})
        with pytest.raises(ValueError, match="Instance is frozen"):
            # ``setattr`` rather than attribute assignment so mypy does
            # not flag ``x`` as undefined: the field comes in via
            # ``extra="allow"`` and is not on the class signature, but
            # the frozen-instance guard runs on every attribute write.
            setattr(payload, "x", 2)  # noqa: B010 -- testing __setattr__ guard

    @pytest.mark.parametrize(
        "payload",
        [
            [],
            ["a", "b"],
            "plain string",
            42,
            3.14,
            True,
            None,
        ],
        ids=[
            "empty-list",
            "list-with-items",
            "string",
            "int",
            "float",
            "bool",
            "null",
        ],
    )
    def test_rejects_non_object_payloads(self, payload: Any) -> None:
        """Non-dict JSON values are rejected at the Pydantic boundary."""
        with pytest.raises(ValidationError):
            WebhookEventPayload.model_validate(payload)


class TestListActivityRoutesThroughService:
    """``list_activity`` no longer touches ``persistence.webhook_receipts``."""

    def test_controller_body_does_not_access_persistence(self) -> None:
        """AST walk: the ``list_activity`` body never reads ``.persistence``."""
        # The helper ``_get_activity_service`` is the one place that
        # bridges from ``persistence.webhook_receipts`` into the service
        # facade. Everywhere else, the controller must route through the
        # service. Walking the AST avoids false positives a substring
        # match would hit (e.g. a comment that mentions "persistence").
        # Litestar's ``@get`` decorator wraps the method into a route
        # handler; the original function is accessible via ``.fn``.
        handler = WebhooksController.list_activity
        source = inspect.getsource(handler.fn)
        tree = ast.parse(inspect.cleandoc(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {
                "persistence",
                "webhook_receipts",
            }:
                pytest.fail(
                    f"list_activity must not access .{node.attr}; "
                    f"route through WebhookActivityService instead.",
                )

    async def test_get_activity_service_caches_per_app_state(self) -> None:
        """Multiple calls reuse the same instance on ``app_state``."""

        class _StubReceiptsRepo:
            pass

        class _StubPersistence:
            webhook_receipts = _StubReceiptsRepo()

        class _StubAppState:
            persistence = _StubPersistence()

        state = {"app_state": _StubAppState()}
        first = await _get_activity_service(state)  # type: ignore[arg-type]
        second = await _get_activity_service(state)  # type: ignore[arg-type]
        assert first is second
        assert isinstance(first, WebhookActivityService)

    def test_module_re_exports_activity_service_accessor(self) -> None:
        """The controller module exposes the lazy service accessor."""
        # The webhooks controller module re-exports the lazy accessor
        # from ``_webhooks_wiring`` so the controller body has a single
        # canonical import. Pin the wire so an accidental rename in the
        # wiring module is caught here.
        assert hasattr(webhooks_module, "_get_activity_service")
