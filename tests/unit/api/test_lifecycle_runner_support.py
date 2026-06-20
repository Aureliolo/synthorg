"""Tests for the startup wiring helpers in ``lifecycle_runner_support``.

These cover the request-path singletons wired once at startup:
``_wire_webhook_request_services`` (replay protector unconditionally,
activity service only on connected persistence) and
``_wire_workflow_execution_service`` (skips with a warning when the
config resolver is absent so the operator sees the cause of the 503).
"""

import pytest

from synthorg.api.lifecycle_runner_support import (
    _wire_webhook_request_services,
    _wire_workflow_execution_service,
)
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.task_engine import TaskEngine
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


class TestWireWebhookRequestServices:
    """Replay protector is unconditional; activity service is gated."""

    def test_replay_protector_wired_without_persistence(self) -> None:
        app_state = make_app_state()
        _wire_webhook_request_services(None, app_state)
        slice_ = app_state.slice(IntegrationsStateSlice)
        # The in-process nonce cache must be a single shared instance, so
        # it is wired regardless of persistence.
        assert slice_.webhook_replay_protector is not None
        # The activity service (read path) needs a backend, so it stays
        # unwired and the controller 503s.
        assert slice_.webhook_activity_service is None

    def test_activity_service_wired_with_connected_persistence(self) -> None:
        persistence = mock_of[PersistenceBackend](is_connected=True)
        app_state = make_app_state()
        _wire_webhook_request_services(persistence, app_state)
        slice_ = app_state.slice(IntegrationsStateSlice)
        assert slice_.webhook_replay_protector is not None
        assert slice_.webhook_activity_service is not None

    def test_idempotent_replay_protector_not_replaced(self) -> None:
        app_state = make_app_state()
        _wire_webhook_request_services(None, app_state)
        first = app_state.slice(IntegrationsStateSlice).webhook_replay_protector
        # A re-entered lifespan must not discard the seen-nonce cache.
        _wire_webhook_request_services(None, app_state)
        assert app_state.slice(IntegrationsStateSlice).webhook_replay_protector is first


class TestWireWorkflowExecutionService:
    """The singleton service needs a resolver, repos, and a task engine."""

    def test_skips_without_config_resolver(self) -> None:
        app_state = make_app_state(
            slices={EngineStateSlice: {"task_engine": mock_of[TaskEngine]()}},
        )
        persistence = mock_of[PersistenceBackend]()
        # No config_resolver on the settings slice -> service stays unwired
        # (the controller 503s) rather than freezing a seed-default depth.
        _wire_workflow_execution_service(persistence, app_state)
        assert app_state.slice(EngineStateSlice).workflow_execution_service is None

    def test_wires_with_resolver_and_repos(self) -> None:
        from synthorg.settings.state import SettingsStateSlice

        app_state = make_app_state(
            slices={
                EngineStateSlice: {"task_engine": mock_of[TaskEngine]()},
                SettingsStateSlice: {
                    "config_resolver": mock_of[ConfigResolver](),
                },
            },
        )
        persistence = mock_of[PersistenceBackend]()
        _wire_workflow_execution_service(persistence, app_state)
        assert app_state.slice(EngineStateSlice).workflow_execution_service is not None

    def test_skips_without_task_engine(self) -> None:
        app_state = make_app_state()
        persistence = mock_of[PersistenceBackend]()
        _wire_workflow_execution_service(persistence, app_state)
        assert app_state.slice(EngineStateSlice).workflow_execution_service is None
