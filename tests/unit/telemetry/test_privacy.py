"""Tests for the telemetry privacy scrubber."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.telemetry.privacy import PrivacyScrubber, PrivacyViolationError
from synthorg.telemetry.protocol import TelemetryEvent


def _make_event(
    event_type: str = "deployment.heartbeat",
    **properties: int | float | str | bool,
) -> TelemetryEvent:
    return TelemetryEvent(
        event_type=event_type,
        deployment_id="test-id",
        synthorg_version="0.6.4",
        python_version="3.14.0",
        os_platform="Linux",
        timestamp=datetime.now(UTC),
        properties=properties,
    )


def _make_raw_event(
    event_type: str = "deployment.heartbeat",
    **properties: object,
) -> TelemetryEvent:
    """Build an event bypassing the model validator.

    ``model_construct`` skips the construction-time property guard so
    the scrubber's *delivery-path* guard can be tested in isolation
    (the post-construction tamper path: a value mutated after a valid
    object was built, or a raw dict that never went through Pydantic).
    """
    return TelemetryEvent.model_construct(
        event_type=event_type,
        deployment_id="test-id",
        synthorg_version="0.6.4",
        python_version="3.14.0",
        os_platform="Linux",
        timestamp=datetime.now(UTC),
        # Intentionally widened: this helper exists to inject the very
        # values the contract forbids, bypassing validation.
        properties=properties,  # type: ignore[arg-type]
    )


@pytest.mark.unit
class TestPrivacyScrubber:
    """Privacy scrubber validation rules."""

    def setup_method(self) -> None:
        self.scrubber = PrivacyScrubber()

    def test_valid_heartbeat_passes(self) -> None:
        event = _make_event(
            "deployment.heartbeat",
            agent_count=5,
            department_count=3,
            team_count=2,
            template_name="startup",
            persistence_backend="sqlite",
            memory_backend="mem0",
            features_enabled="meeting,delegation",
            uptime_hours=12.5,
        )
        result = self.scrubber.validate(event)
        assert result is event

    def test_valid_session_summary_passes(self) -> None:
        event = _make_event(
            "deployment.session_summary",
            tasks_created=10,
            tasks_completed=8,
            tasks_failed=2,
            error_rate_limit=1,
            error_timeout=0,
            error_connection=0,
            error_internal=1,
            error_validation=0,
            error_other=0,
            provider_count=2,
            topology_hierarchical=3,
            topology_parallel=1,
            topology_sequential=0,
            topology_auto=5,
            meetings_held=2,
            delegations_executed=4,
            uptime_hours=24.0,
        )
        result = self.scrubber.validate(event)
        assert result is event

    def test_valid_startup_passes(self) -> None:
        event = _make_event(
            "deployment.startup",
            agent_count=3,
            department_count=2,
            template_name="enterprise",
            persistence_backend="postgresql",
            memory_backend="custom",
        )
        result = self.scrubber.validate(event)
        assert result is event

    def test_valid_startup_with_docker_info_passes(self) -> None:
        event = _make_event(
            "deployment.startup",
            agent_count=0,
            department_count=0,
            template_name="",
            persistence_backend="sqlite",
            memory_backend="mem0",
            docker_info_available=True,
            docker_server_version="27.3.1",
            docker_operating_system="Docker Desktop",
            docker_os_type="linux",
            docker_os_version="",
            docker_architecture="x86_64",
            docker_kernel_version="6.10.14-linuxkit",
            docker_storage_driver="overlay2",
            docker_default_runtime="runc",
            docker_isolation="",
            docker_ncpu=8,
            docker_mem_total=8589934592,
            docker_gpu_runtime_nvidia_available=False,
        )
        result = self.scrubber.validate(event)
        assert result is event

    def test_valid_startup_with_unavailable_docker_marker_passes(self) -> None:
        event = _make_event(
            "deployment.startup",
            agent_count=0,
            department_count=0,
            template_name="",
            persistence_backend="sqlite",
            memory_backend="mem0",
            docker_info_available=False,
            docker_info_unavailable_reason="socket_not_mounted",
        )
        result = self.scrubber.validate(event)
        assert result is event

    def test_valid_shutdown_passes(self) -> None:
        event = _make_event(
            "deployment.shutdown",
            uptime_hours=48.0,
            graceful=True,
        )
        result = self.scrubber.validate(event)
        assert result is event

    def test_rejects_unknown_event_type(self) -> None:
        event = _make_event("user.logged_in")
        with pytest.raises(PrivacyViolationError, match="Disallowed event type"):
            self.scrubber.validate(event)

    def test_rejects_unknown_property_key(self) -> None:
        # Construction now rejects this; the scrubber guards the
        # delivery path for a raw/tampered event that skipped it.
        event = _make_raw_event(
            "deployment.heartbeat",
            agent_count=5,
            unknown_field=42,
        )
        with pytest.raises(PrivacyViolationError, match="Disallowed property"):
            self.scrubber.validate(event)

    @pytest.mark.parametrize(
        "bad_key",
        [
            "api_key",
            "secret_value",
            "jwt_token",
            "user_password",
            "message_content",
            "task_description",
            "system_prompt",
            "bearer_credential",
            "auth_header",
        ],
    )
    def test_rejects_forbidden_key_patterns(
        self, bad_key: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even if somehow added to the allowlist, forbidden
        patterns are caught.
        """
        from types import MappingProxyType

        from synthorg.telemetry import property_rules

        # Patch the single-source allowlist so the key passes the
        # allowlist check and the *forbidden-pattern* rule is what
        # fires. A raw event skips construction-time validation so the
        # scrubber's delivery-path guard is the one under test.
        original = property_rules._ALLOWED_PROPERTIES["deployment.heartbeat"]
        patched = dict(property_rules._ALLOWED_PROPERTIES)
        patched["deployment.heartbeat"] = original | {bad_key}
        monkeypatch.setattr(
            property_rules,
            "_ALLOWED_PROPERTIES",
            MappingProxyType(patched),
        )
        event_with_bad = _make_raw_event("deployment.heartbeat", **{bad_key: "value"})
        with pytest.raises(PrivacyViolationError, match="Forbidden pattern"):
            self.scrubber.validate(event_with_bad)

    def test_rejects_long_string_values(self) -> None:
        # Raw event skips construction-time validation; the scrubber
        # still catches the over-length string at the delivery path.
        event = _make_raw_event(
            "deployment.heartbeat",
            template_name="x" * 100,
        )
        with pytest.raises(PrivacyViolationError, match="exceeds"):
            self.scrubber.validate(event)

    def test_accepts_max_length_string(self) -> None:
        event = _make_event(
            "deployment.heartbeat",
            template_name="x" * 64,
        )
        result = self.scrubber.validate(event)
        assert result is event

    def test_empty_properties_passes(self) -> None:
        event = _make_event("deployment.heartbeat")
        result = self.scrubber.validate(event)
        assert result is event


@pytest.mark.unit
class TestTelemetryEventConstructionGuard:
    """The property contract is enforced at construction (REWORK #11).

    A telemetry property typo no longer silently drops downstream --
    it raises ``ValidationError`` where the event is built.
    """

    def test_unknown_property_raises_at_construction(self) -> None:
        with pytest.raises(ValidationError, match="Disallowed property"):
            _make_event("deployment.heartbeat", agent_count=5, oops=1)

    def test_over_length_string_raises_at_construction(self) -> None:
        with pytest.raises(ValidationError, match="exceeds"):
            _make_event("deployment.heartbeat", template_name="x" * 100)

    def test_forbidden_pattern_raises_at_construction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import MappingProxyType

        from synthorg.telemetry import property_rules

        original = property_rules._ALLOWED_PROPERTIES["deployment.heartbeat"]
        patched = dict(property_rules._ALLOWED_PROPERTIES)
        patched["deployment.heartbeat"] = original | {"api_token"}
        monkeypatch.setattr(
            property_rules,
            "_ALLOWED_PROPERTIES",
            MappingProxyType(patched),
        )
        with pytest.raises(ValidationError, match="Forbidden pattern"):
            _make_event("deployment.heartbeat", api_token="v")

    def test_valid_event_constructs(self) -> None:
        event = _make_event(
            "deployment.heartbeat",
            agent_count=5,
            uptime_hours=1.0,
        )
        assert event.properties["agent_count"] == 5

    def test_unknown_event_type_with_no_properties_constructs(self) -> None:
        # event_type allowlisting is the scrubber's delivery-path
        # concern; construction only guards properties, so an event
        # with no properties for an unknown type still builds.
        event = _make_event("user.logged_in")
        assert event.properties == {}
