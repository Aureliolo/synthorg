"""Webhook controller routes through the service layer and typed boundary.

Two structural invariants are pinned:

1. ``receive_webhook`` parses the untrusted body through a typed
   :class:`WebhookEventPayload` boundary that rejects non-JSON bodies and
   non-object payloads (arrays, scalars, ``null``).
2. ``list_activity`` never reaches into ``state["app_state"].persistence``;
   it routes through the startup-wired :class:`WebhookActivityService`
   read from ``IntegrationsStateSlice``.

A static AST walk verifies the controller never touches
``persistence.webhook_receipts``. Parametrised model tests verify the
Pydantic boundary rejects non-object payloads.
"""

import ast
import inspect

import pytest
from pydantic import JsonValue, ValidationError

from synthorg.api.controllers._webhooks_wiring import WebhookEventPayload
from synthorg.api.controllers.webhooks import activity as webhooks_activity
from synthorg.api.controllers.webhooks.activity import WebhooksActivityController
from synthorg.api.lifecycle_runner_support import _wire_webhook_request_services
from synthorg.integrations.state import (
    IntegrationsStateSlice,
    webhook_activity_service_of,
    webhook_replay_protector_of,
)
from synthorg.integrations.webhooks.activity_service import (
    WebhookActivityService,
)
from synthorg.integrations.webhooks.replay_protection import ReplayProtector
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import make_app_state, mock_of

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
    def test_rejects_non_object_payloads(self, payload: JsonValue) -> None:
        """Non-dict JSON values are rejected at the Pydantic boundary."""
        with pytest.raises(ValidationError):
            WebhookEventPayload.model_validate(payload)


class TestListActivityRoutesThroughService:
    """``list_activity`` no longer touches ``persistence.webhook_receipts``."""

    def test_controller_body_does_not_access_persistence(self) -> None:
        """AST walk: the ``list_activity`` body never reads ``.persistence``."""
        # The startup wirer (``_wire_webhook_request_services``) is the one
        # place that bridges ``persistence.webhook_receipts`` into the
        # service facade. The controller must route through the wired
        # service via ``webhook_activity_service_of``. Walking the AST
        # avoids false positives a substring match would hit (e.g. a comment
        # that mentions "persistence"). Litestar's ``@get`` decorator wraps
        # the method into a route handler; the original function is
        # accessible via ``.fn``.
        handler = WebhooksActivityController.list_activity
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

    def test_startup_wiring_publishes_singletons_idempotently(self) -> None:
        """``_wire_webhook_request_services`` wires both singletons once."""

        class _StubReceiptsRepo:
            pass

        persistence = mock_of[PersistenceBackend](
            is_connected=True,
            webhook_receipts=_StubReceiptsRepo(),
        )
        app_state = make_app_state(persistence=persistence)

        _wire_webhook_request_services(persistence, app_state)
        activity = webhook_activity_service_of(app_state)
        protector = webhook_replay_protector_of(app_state)
        assert isinstance(activity, WebhookActivityService)
        assert isinstance(protector, ReplayProtector)

        # Second wiring pass must NOT replace the instances: the
        # protector's seen-nonce cache is the source of truth between
        # durable-idempotency reads and a re-entry into lifespan must not
        # discard it.
        _wire_webhook_request_services(persistence, app_state)
        assert webhook_activity_service_of(app_state) is activity
        assert webhook_replay_protector_of(app_state) is protector

    def test_replay_protector_wired_even_without_persistence(self) -> None:
        """The config-only protector wires regardless of persistence."""
        app_state = make_app_state(persistence=None)
        _wire_webhook_request_services(None, app_state)
        assert isinstance(
            webhook_replay_protector_of(app_state),
            ReplayProtector,
        )
        # The activity service stays unwired (read path needs persistence),
        # so its accessor 503s rather than returning a half-built service.
        assert app_state.slice(IntegrationsStateSlice).webhook_activity_service is None

    def test_activity_controller_imports_service_accessor(self) -> None:
        """The activity sub-controller binds the slice accessor."""
        # ``activity`` reads the startup-wired service via
        # ``webhook_activity_service_of`` as a bare module global, so the
        # controller body has a single canonical import. Pin the wire so an
        # accidental rename in the integrations state module is caught here.
        assert hasattr(webhooks_activity, "webhook_activity_service_of")
