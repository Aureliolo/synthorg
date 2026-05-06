"""Unit tests for the custom rules API controller."""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import structlog

from synthorg.api.controllers.custom_rules import (
    CreateCustomRuleRequest,
    CustomRuleController,
    PreviewRuleRequest,
    UpdateCustomRuleRequest,
    _build_preview_snapshot,
    _metric_to_dict,
    rule_to_dict,
)
from synthorg.core.domain_errors import NotFoundError
from synthorg.meta.models import ProposalAltitude, RuleSeverity
from synthorg.meta.rules.custom import (
    METRIC_REGISTRY,
    Comparator,
    CustomRuleDefinition,
    DeclarativeRule,
    resolve_metric,
)
from synthorg.meta.rules.service import CustomRuleNotFoundError, CustomRulesService
from synthorg.observability.events.security import (
    SECURITY_CUSTOM_RULE_CREATED,
    SECURITY_CUSTOM_RULE_DELETED,
    SECURITY_CUSTOM_RULE_TOGGLED,
    SECURITY_CUSTOM_RULE_UPDATED,
)

pytestmark = pytest.mark.unit


# ── Controller routes ─────────────────────────────────────────────


class TestCustomRuleControllerRoutes:
    """Verify CustomRuleController route definitions."""

    def test_controller_path(self) -> None:
        assert CustomRuleController.path == "/meta/custom-rules"

    @pytest.mark.parametrize(
        ("method_name", "expected_path", "expected_method"),
        [
            ("list_rules", "/", "GET"),
            ("get_rule", "/{rule_id:str}", "GET"),
            ("create_rule", "/", "POST"),
            ("update_rule", "/{rule_id:str}", "PATCH"),
            ("delete_rule", "/{rule_id:str}", "DELETE"),
            ("toggle_rule", "/{rule_id:str}/toggle", "POST"),
            ("list_metrics", "/metrics", "GET"),
            ("preview_rule", "/preview", "POST"),
        ],
    )
    def test_has_endpoint(
        self,
        method_name: str,
        expected_path: str,
        expected_method: str,
    ) -> None:
        handler = getattr(CustomRuleController, method_name, None)
        assert handler is not None, f"Missing handler: {method_name}"
        assert expected_path in handler.paths, (
            f"{method_name}: expected path {expected_path!r}, got {handler.paths}"
        )
        assert expected_method in handler.http_methods, (
            f"{method_name}: expected method {expected_method!r}, "
            f"got {handler.http_methods}"
        )


# ── Request DTOs ──────────────────────────────────────────────────


class TestCreateCustomRuleRequest:
    """Validate CreateCustomRuleRequest DTO."""

    def test_valid(self) -> None:
        req = CreateCustomRuleRequest(
            name="my-rule",
            description="Fires when quality drops",
            metric_path="performance.avg_quality_score",
            comparator=Comparator.LT,
            threshold=5.0,
            severity=RuleSeverity.WARNING,
            target_altitudes=(ProposalAltitude.CONFIG_TUNING,),
        )
        assert req.name == "my-rule"
        assert req.comparator == Comparator.LT

    def test_requires_at_least_one_altitude(self) -> None:
        with pytest.raises(ValueError, match="at least"):
            CreateCustomRuleRequest(
                name="bad-rule",
                description="No altitudes",
                metric_path="performance.avg_quality_score",
                comparator=Comparator.LT,
                threshold=5.0,
                severity=RuleSeverity.WARNING,
                target_altitudes=(),
            )


class TestUpdateCustomRuleRequest:
    """Validate UpdateCustomRuleRequest DTO."""

    def test_all_optional(self) -> None:
        req = UpdateCustomRuleRequest()
        assert req.name is None
        assert req.threshold is None

    def test_partial_update(self) -> None:
        req = UpdateCustomRuleRequest(
            threshold=9.0,
            severity=RuleSeverity.CRITICAL,
        )
        assert req.threshold == 9.0
        assert req.severity == RuleSeverity.CRITICAL
        assert req.name is None


class TestPreviewRuleRequest:
    """Validate PreviewRuleRequest DTO."""

    def test_valid(self) -> None:
        req = PreviewRuleRequest(
            metric_path="performance.avg_quality_score",
            comparator=Comparator.LT,
            threshold=5.0,
            sample_value=3.0,
        )
        assert req.sample_value == 3.0


# ── Serialization helpers ─────────────────────────────────────────


class TestSerializationHelpers:
    """Test rule_to_dict and _metric_to_dict."""

    def test_rule_to_dict(self) -> None:
        now = datetime.now(UTC)
        defn = CustomRuleDefinition(
            id=uuid4(),
            name="test",
            description="Test rule",
            metric_path="performance.avg_quality_score",
            comparator=Comparator.GT,
            threshold=8.0,
            severity=RuleSeverity.INFO,
            target_altitudes=(
                ProposalAltitude.CONFIG_TUNING,
                ProposalAltitude.ARCHITECTURE,
            ),
            created_at=now,
            updated_at=now,
        )
        d = rule_to_dict(defn)
        assert d["name"] == "test"
        assert d["comparator"] == "gt"
        assert d["severity"] == "info"
        assert d["target_altitudes"] == [
            "config_tuning",
            "architecture",
        ]
        assert d["enabled"] is True

    def test_metric_to_dict(self) -> None:
        metric = METRIC_REGISTRY[0]
        d = _metric_to_dict(metric)
        assert d["path"] == metric.path
        assert d["label"] == metric.label
        assert d["domain"] == metric.domain
        assert "value_type" in d
        assert "nullable" in d


# ── Preview snapshot builder ──────────────────────────────────────


class TestBuildPreviewSnapshot:
    """Test _build_preview_snapshot utility."""

    @pytest.mark.parametrize(
        ("metric_path", "sample_input", "expected_value", "expected_type"),
        [
            ("performance.avg_quality_score", 3.5, 3.5, None),
            ("budget.days_until_exhausted", 7.0, 7, int),
            ("coordination.coordination_overhead_pct", 45.0, 45.0, None),
            ("errors.total_findings", 15.0, 15, int),
            ("telemetry.event_count", 200.0, 200, int),
        ],
    )
    def test_domain_metric(
        self,
        metric_path: str,
        sample_input: float,
        expected_value: float | int,
        expected_type: type | None,
    ) -> None:
        snap = _build_preview_snapshot(metric_path, sample_input)
        val = resolve_metric(snap, metric_path)
        assert val == expected_value
        if expected_type is not None:
            assert isinstance(val, expected_type)

    @pytest.mark.parametrize(
        "metric_path",
        [m.path for m in METRIC_REGISTRY],
    )
    def test_all_registry_metrics_buildable(
        self,
        metric_path: str,
    ) -> None:
        """Every registered metric can produce a valid snapshot."""
        snap = _build_preview_snapshot(metric_path, 1.0)
        val = resolve_metric(snap, metric_path)
        assert val is not None


# ── Preview rule evaluation ───────────────────────────────────────


class TestPreviewEvaluation:
    """Test that preview evaluation works end-to-end."""

    @pytest.mark.parametrize(
        ("sample_value", "should_fire"),
        [
            (3.0, True),
            (7.0, False),
        ],
    )
    def test_preview_evaluation(
        self,
        sample_value: float,
        *,
        should_fire: bool,
    ) -> None:
        now = datetime.now(UTC)
        defn = CustomRuleDefinition(
            name="preview",
            description="Preview rule",
            metric_path="performance.avg_quality_score",
            comparator=Comparator.LT,
            threshold=5.0,
            severity=RuleSeverity.INFO,
            target_altitudes=(ProposalAltitude.CONFIG_TUNING,),
            created_at=now,
            updated_at=now,
        )
        rule = DeclarativeRule(defn)
        snap = _build_preview_snapshot(
            "performance.avg_quality_score",
            sample_value,
        )
        match = rule.evaluate(snap)
        if should_fire:
            assert match is not None
            assert match.signal_context["metric_value"] == sample_value
        else:
            assert match is None


# ── Audit-chain coverage ──────────────────────────────────────────


def _make_rule(
    *,
    name: str = "test-rule",
    enabled: bool = True,
) -> CustomRuleDefinition:
    now = datetime.now(UTC)
    return CustomRuleDefinition(
        id=uuid4(),
        name=name,
        description="probe",
        metric_path="performance.avg_quality_score",
        comparator=Comparator.LT,
        threshold=5.0,
        severity=RuleSeverity.WARNING,
        target_altitudes=(ProposalAltitude.CONFIG_TUNING,),
        enabled=enabled,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def patched_service(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Stub ``CustomRulesService`` so controller calls hit our mock."""
    service = MagicMock(spec=CustomRulesService)
    monkeypatch.setattr(
        "synthorg.api.controllers.custom_rules.CustomRulesService",
        lambda *args, **kwargs: service,
    )
    return service


def _state_with_persistence() -> MagicMock:
    """Build a controller-friendly ``state`` with a stubbed persistence.

    ``custom_rules._service`` reads ``state.app_state.persistence.custom_rules``
    via attribute access (Litestar ``State`` supports both attribute and
    mapping access); the real controller wires a service from that
    repo, but the test patches ``CustomRulesService`` to bypass the
    real one. The persistence chain is bare because the patched
    ``CustomRulesService`` factory ignores its repo argument; the chain
    only needs to exist for ``state.app_state.persistence.custom_rules``
    to resolve without raising ``AttributeError``.
    """
    from synthorg.api.state import AppState
    from synthorg.persistence.custom_rule_protocol import CustomRuleRepository
    from synthorg.persistence.protocol import PersistenceBackend

    state = MagicMock(spec=["app_state"])
    state.app_state = MagicMock(spec=AppState)
    state.app_state.persistence = MagicMock(spec=PersistenceBackend)
    state.app_state.persistence.custom_rules = MagicMock(spec=CustomRuleRepository)
    return state


@pytest.mark.unit
class TestCustomRuleAuditEvents:
    """Custom rule mutations emit ``security.custom_rule.*`` events.

    The ``AuditChainSink`` filters on the ``security.*`` prefix; events
    in the ``meta.*`` namespace are operational-only and never reach the
    audit chain. Custom rules are control-plane mutations comparable in
    impact to settings or autonomy changes, so each lifecycle hop is
    signed.
    """

    async def test_create_emits_security_event_with_payload(
        self,
        patched_service: MagicMock,
    ) -> None:
        """Create success carries the bare ``rule`` field (matching
        SECURITY_PROVIDER_* naming) plus rule_name / metric_path /
        severity context. A future log refactor that drops one of these
        fields would break forensic queries -- the payload assertions
        pin the contract."""
        rule = _make_rule()
        patched_service.create.return_value = rule

        ctrl = CustomRuleController(owner=CustomRuleController)  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as events:
            await ctrl.create_rule.fn(
                ctrl,
                state=_state_with_persistence(),
                data=CreateCustomRuleRequest(
                    name="test-rule",
                    description="probe",
                    metric_path="performance.avg_quality_score",
                    comparator=Comparator.LT,
                    threshold=5.0,
                    severity=RuleSeverity.WARNING,
                    target_altitudes=(ProposalAltitude.CONFIG_TUNING,),
                ),
            )

        matches = [e for e in events if e["event"] == SECURITY_CUSTOM_RULE_CREATED]
        assert len(matches) == 1
        emission = matches[0]
        assert emission["rule"] == str(rule.id)
        assert emission["rule_name"] == rule.name
        assert emission["metric_path"] == rule.metric_path
        assert emission["severity"] == rule.severity.value

    async def test_update_emits_security_event_with_fields_changed(
        self,
        patched_service: MagicMock,
    ) -> None:
        """Update success carries the bare ``rule`` field and
        ``fields_changed`` reflecting only the partial-update keys."""
        rule = _make_rule()
        patched_service.update.return_value = rule

        ctrl = CustomRuleController(owner=CustomRuleController)  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as events:
            await ctrl.update_rule.fn(
                ctrl,
                state=_state_with_persistence(),
                rule_id=str(rule.id),
                data=UpdateCustomRuleRequest(threshold=9.0),
            )

        matches = [e for e in events if e["event"] == SECURITY_CUSTOM_RULE_UPDATED]
        assert len(matches) == 1
        emission = matches[0]
        assert emission["rule"] == str(rule.id)
        assert emission["fields_changed"] == ["threshold"]

    async def test_delete_emits_security_event(
        self,
        patched_service: MagicMock,
    ) -> None:
        rule = _make_rule()
        patched_service.delete.return_value = None

        ctrl = CustomRuleController(owner=CustomRuleController)  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as events:
            await ctrl.delete_rule.fn(
                ctrl,
                state=_state_with_persistence(),
                rule_id=str(rule.id),
            )

        matches = [e for e in events if e["event"] == SECURITY_CUSTOM_RULE_DELETED]
        assert len(matches) == 1
        assert matches[0]["rule"] == str(rule.id)

    async def test_toggle_emits_security_event(
        self,
        patched_service: MagicMock,
    ) -> None:
        rule = _make_rule(enabled=False)
        patched_service.toggle.return_value = rule

        ctrl = CustomRuleController(owner=CustomRuleController)  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as events:
            await ctrl.toggle_rule.fn(
                ctrl,
                state=_state_with_persistence(),
                rule_id=str(rule.id),
            )

        matches = [e for e in events if e["event"] == SECURITY_CUSTOM_RULE_TOGGLED]
        assert len(matches) == 1
        emission = matches[0]
        assert emission["rule"] == str(rule.id)
        assert emission["enabled"] is False

    async def test_delete_missing_does_not_emit_security_event(
        self,
        patched_service: MagicMock,
    ) -> None:
        """Failed delete (rule missing) raises and emits no SECURITY_*."""
        patched_service.delete.side_effect = CustomRuleNotFoundError("nope")

        ctrl = CustomRuleController(owner=CustomRuleController)  # type: ignore[arg-type]
        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(NotFoundError),
        ):
            await ctrl.delete_rule.fn(
                ctrl,
                state=_state_with_persistence(),
                rule_id="missing",
            )

        emitted = [e["event"] for e in events]
        assert SECURITY_CUSTOM_RULE_DELETED not in emitted
