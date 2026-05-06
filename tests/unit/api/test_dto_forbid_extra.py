"""Every API-boundary DTO must reject unknown fields (``extra="forbid"``).

A DTO that does not declare ``extra="forbid"`` silently accepts unknown
payload keys, which masks client typos and lets fabricated capability
flags slip through to handler logic. ``scripts/check_dto_forbid_extra.py``
enforces the convention statically; this test asserts the runtime
behaviour for every Request / Response / Snapshot / Result / Envelope /
Status / Info / Summary DTO under ``src/synthorg/api/``, plus a small
suite of gate-classification tests that exercise the script directly.

The bare-extra-key probe uses an empty otherwise-invalid payload on
purpose: Pydantic still records the ``extra_forbidden`` error alongside
any required-field misses, so the assertion is robust to required-field
changes in the surrounding DTO.
"""

import importlib.util
import textwrap
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from synthorg.api.auth.controller_dtos import (
    ChangePasswordRequest,
    CookieSessionResponse,
    LoginRequest,
    SessionResponse,
    SetupRequest,
    UserInfoResponse,
    WsTicketResponse,
)
from synthorg.api.controllers.agents import (
    AgentHealthResponse,
    PerformanceSummary,
    TrustSummary,
)
from synthorg.api.controllers.analytics import (
    ForecastResponse,
    TrendsResponse,
)
from synthorg.api.controllers.autonomy import (
    AutonomyLevelRequest,
    AutonomyLevelResponse,
)
from synthorg.api.controllers.budget import (
    CostRecordListResponse,
    DailySummary,
    PeriodSummary,
)
from synthorg.api.controllers.capabilities import CapabilitiesResponse
from synthorg.api.controllers.collaboration import (
    CalibrationSummaryResponse,
    OverrideResponse,
    SetOverrideRequest,
)
from synthorg.api.controllers.custom_rules import (
    CreateCustomRuleRequest,
    PreviewRuleRequest,
    UpdateCustomRuleRequest,
)
from synthorg.api.controllers.escalations import (
    CancelEscalationRequest,
    EscalationResponse,
    SubmitDecisionRequest,
)
from synthorg.api.controllers.events import (
    InterruptResponse,
    ResumeInterruptRequest,
)
from synthorg.api.controllers.health import (
    LivenessStatus,
    ReadinessStatus,
)
from synthorg.api.controllers.mcp_catalog import InstallEntryResponse
from synthorg.api.controllers.meetings import TriggerMeetingRequest
from synthorg.api.controllers.memory import ActiveEmbedderResponse
from synthorg.api.controllers.meta import ChatRequest
from synthorg.api.controllers.quality import (
    QualityOverrideResponse,
    SetQualityOverrideRequest,
)
from synthorg.api.controllers.reports import (
    GenerateReportRequest,
    ReportResponse,
)
from synthorg.api.controllers.reviews import StageDecisionResult
from synthorg.api.controllers.scaling import (
    PriorityUpdateRequest,
    ScalingDecisionResponse,
    ScalingSignalResponse,
    ScalingStrategyResponse,
    StrategyUpdateRequest,
)
from synthorg.api.controllers.settings import (
    SecurityConfigExportResponse,
    SecurityConfigImportRequest,
    UpdateSettingRequest,
)

# Aliases to avoid pytest's ``Test``-prefix auto-collection (pytest tries
# to instantiate any module-level ``Test*`` symbol as a test class).
from synthorg.api.controllers.settings import (
    TestSinkConfigRequest as _SinkConfigRequest,
)
from synthorg.api.controllers.settings import (
    TestSinkConfigResponse as _SinkConfigResponse,
)
from synthorg.api.controllers.setup_models import (
    AvailableLocalesResponse,
    PersonalityPresetInfoResponse,
    PersonalityPresetsListResponse,
    SetupAgentResponse,
    SetupAgentsListResponse,
    SetupAgentSummary,
    SetupCompanyResponse,
    SetupCompleteResponse,
    SetupNameLocalesResponse,
    SetupStatusResponse,
    TemplateInfoResponse,
    TemplateVariableResponse,
)
from synthorg.api.controllers.simulations import SimulationStatusResponse
from synthorg.api.controllers.subworkflows import CreateSubworkflowRequest
from synthorg.api.controllers.teams import TeamResponse
from synthorg.api.controllers.template_packs import (
    ApplyTemplatePackResponse,
    PackInfoResponse,
)
from synthorg.api.controllers.users import (
    CreateUserRequest,
    GrantOrgRoleRequest,
    UpdateUserRoleRequest,
    UserResponse,
)
from synthorg.api.dto import (
    ApiResponse,
    ApproveRequest,
    CancelTaskRequest,
    CoordinateTaskRequest,
    CoordinationPhaseResponse,
    CoordinationResultResponse,
    CreateApprovalRequest,
    CreateArtifactRequest,
    CreateProjectRequest,
    CreateTaskRequest,
    PaginatedResponse,
    RejectRequest,
    RollbackAgentIdentityRequest,
    TransitionTaskRequest,
    UpdateTaskRequest,
)
from synthorg.api.dto_discovery import (
    AddAllowlistEntryRequest,
    DiscoveryPolicyResponse,
    RemoveAllowlistEntryRequest,
)
from synthorg.api.dto_ontology import (
    CreateEntityRequest,
    DriftAgentResponse,
    DriftReportResponse,
    DriftSummary,
    EntityFieldResponse,
    EntityRelationResponse,
    EntityResponse,
    EntityVersionResponse,
    UpdateEntityRequest,
)
from synthorg.api.dto_personalities import (
    PresetDetailResponse,
    PresetSummaryResponse,
)
from synthorg.api.dto_training import (
    CreateTrainingPlanRequest,
    TrainingPlanResponse,
    TrainingResultResponse,
    UpdateTrainingOverridesRequest,
)
from synthorg.api.dto_workflow import (
    ActivateWorkflowRequest,
    BlueprintInfoResponse,
    CreateFromBlueprintRequest,
    CreateWorkflowDefinitionRequest,
    RollbackWorkflowRequest,
    UpdateWorkflowDefinitionRequest,
    WorkflowIODeclarationRequest,
)

pytestmark = pytest.mark.unit

# Every ``*Request`` Pydantic DTO under ``src/synthorg/api/``.
# Each MUST have ``ConfigDict(..., extra="forbid")``.
REQUEST_DTOS: tuple[type[BaseModel], ...] = (
    # auth/controller_dtos.py
    SetupRequest,
    LoginRequest,
    ChangePasswordRequest,
    # controllers/* inline DTOs
    AutonomyLevelRequest,
    SetOverrideRequest,
    CreateCustomRuleRequest,
    UpdateCustomRuleRequest,
    PreviewRuleRequest,
    SubmitDecisionRequest,
    CancelEscalationRequest,
    ResumeInterruptRequest,
    TriggerMeetingRequest,
    ChatRequest,
    SetQualityOverrideRequest,
    GenerateReportRequest,
    StrategyUpdateRequest,
    PriorityUpdateRequest,
    UpdateSettingRequest,
    _SinkConfigRequest,
    SecurityConfigImportRequest,
    CreateSubworkflowRequest,
    CreateUserRequest,
    UpdateUserRoleRequest,
    GrantOrgRoleRequest,
    # dto.py
    CreateArtifactRequest,
    CreateProjectRequest,
    CreateTaskRequest,
    UpdateTaskRequest,
    TransitionTaskRequest,
    CancelTaskRequest,
    CreateApprovalRequest,
    ApproveRequest,
    RejectRequest,
    CoordinateTaskRequest,
    RollbackAgentIdentityRequest,
    # dto_discovery.py
    AddAllowlistEntryRequest,
    RemoveAllowlistEntryRequest,
    # dto_ontology.py
    CreateEntityRequest,
    UpdateEntityRequest,
    # dto_training.py
    CreateTrainingPlanRequest,
    UpdateTrainingOverridesRequest,
    # dto_workflow.py
    WorkflowIODeclarationRequest,
    CreateWorkflowDefinitionRequest,
    UpdateWorkflowDefinitionRequest,
    ActivateWorkflowRequest,
    CreateFromBlueprintRequest,
    RollbackWorkflowRequest,
)

# Every Response / Snapshot / Result / Envelope / Status / Info /
# Summary Pydantic DTO under ``src/synthorg/api/``.  Each MUST have
# ``ConfigDict(..., extra="forbid")`` enforced by
# ``scripts/check_dto_forbid_extra.py``.
RESPONSE_DTOS: tuple[type[BaseModel], ...] = (
    # auth/controller_dtos.py
    CookieSessionResponse,
    UserInfoResponse,
    WsTicketResponse,
    SessionResponse,
    # controllers/agents.py
    TrustSummary,
    PerformanceSummary,
    AgentHealthResponse,
    # controllers/analytics.py
    TrendsResponse,
    ForecastResponse,
    # controllers/autonomy.py
    AutonomyLevelResponse,
    # controllers/budget.py
    DailySummary,
    PeriodSummary,
    CostRecordListResponse,
    # controllers/capabilities.py
    CapabilitiesResponse,
    # controllers/collaboration.py
    OverrideResponse,
    CalibrationSummaryResponse,
    # controllers/escalations.py
    EscalationResponse,
    # controllers/events.py
    InterruptResponse,
    # controllers/health.py
    LivenessStatus,
    ReadinessStatus,
    # controllers/mcp_catalog.py
    InstallEntryResponse,
    # controllers/memory.py
    ActiveEmbedderResponse,
    # controllers/quality.py
    QualityOverrideResponse,
    # controllers/reports.py
    ReportResponse,
    # controllers/reviews.py
    StageDecisionResult,
    # controllers/scaling.py
    ScalingStrategyResponse,
    ScalingSignalResponse,
    ScalingDecisionResponse,
    # controllers/settings.py
    _SinkConfigResponse,
    SecurityConfigExportResponse,
    # controllers/setup_models.py
    SetupStatusResponse,
    TemplateVariableResponse,
    TemplateInfoResponse,
    SetupAgentSummary,
    SetupCompanyResponse,
    SetupAgentResponse,
    PersonalityPresetInfoResponse,
    PersonalityPresetsListResponse,
    SetupAgentsListResponse,
    SetupNameLocalesResponse,
    AvailableLocalesResponse,
    SetupCompleteResponse,
    # controllers/simulations.py
    SimulationStatusResponse,
    # controllers/teams.py
    TeamResponse,
    # controllers/template_packs.py
    PackInfoResponse,
    ApplyTemplatePackResponse,
    # controllers/users.py
    UserResponse,
    # dto.py
    ApiResponse,
    PaginatedResponse,
    CoordinationPhaseResponse,
    CoordinationResultResponse,
    # dto_discovery.py
    DiscoveryPolicyResponse,
    # dto_ontology.py
    EntityFieldResponse,
    EntityRelationResponse,
    EntityResponse,
    EntityVersionResponse,
    DriftAgentResponse,
    DriftReportResponse,
    DriftSummary,
    # dto_personalities.py
    PresetSummaryResponse,
    PresetDetailResponse,
    # dto_training.py
    TrainingPlanResponse,
    TrainingResultResponse,
    # dto_workflow.py
    BlueprintInfoResponse,
)


@pytest.mark.parametrize("model_cls", REQUEST_DTOS, ids=lambda c: c.__name__)
def test_request_dto_rejects_unknown_field(model_cls: type[BaseModel]) -> None:
    """Each request DTO surfaces ``extra_forbidden`` for unknown keys."""
    payload: dict[str, Any] = {"synthorg_unexpected_field": "x"}
    with pytest.raises(ValidationError) as exc_info:
        model_cls.model_validate(payload)
    error_types = {err["type"] for err in exc_info.value.errors()}
    assert "extra_forbidden" in error_types, (
        f"{model_cls.__name__} accepted an unknown field; expected "
        f"'extra_forbidden' in {error_types}.  Add ``extra=\"forbid\"`` to "
        f"its ``ConfigDict`` so the API boundary rejects typos and "
        f"fabricated capability flags."
    )


@pytest.mark.parametrize("model_cls", REQUEST_DTOS, ids=lambda c: c.__name__)
def test_request_dto_config_declares_forbid(model_cls: type[BaseModel]) -> None:
    """Belt + braces: the config object itself must declare extra=forbid.

    Catches subclass-shadowing bugs where a parent forbids extras but a
    subclass quietly relaxes them.
    """
    extra = model_cls.model_config.get("extra")
    assert extra == "forbid", (
        f"{model_cls.__name__}.model_config['extra'] = {extra!r}; expected 'forbid'."
    )


@pytest.mark.parametrize("model_cls", RESPONSE_DTOS, ids=lambda c: c.__name__)
def test_response_dto_rejects_unknown_field(model_cls: type[BaseModel]) -> None:
    """Each response DTO surfaces ``extra_forbidden`` for unknown keys."""
    payload: dict[str, Any] = {"synthorg_unexpected_field": "x"}
    with pytest.raises(ValidationError) as exc_info:
        model_cls.model_validate(payload)
    error_types = {err["type"] for err in exc_info.value.errors()}
    assert "extra_forbidden" in error_types, (
        f"{model_cls.__name__} accepted an unknown field; expected "
        f"'extra_forbidden' in {error_types}.  Add ``extra=\"forbid\"`` to "
        f"its ``ConfigDict`` so the API boundary rejects fabricated "
        f"server-side fields and protects round-trip clients."
    )


@pytest.mark.parametrize("model_cls", RESPONSE_DTOS, ids=lambda c: c.__name__)
def test_response_dto_config_declares_forbid(model_cls: type[BaseModel]) -> None:
    """Belt + braces: response DTO config must declare extra=forbid."""
    extra = model_cls.model_config.get("extra")
    assert extra == "forbid", (
        f"{model_cls.__name__}.model_config['extra'] = {extra!r}; expected 'forbid'."
    )


# ── Gate-classification tests (exercise the script directly) ─────────


_GATE_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "check_dto_forbid_extra.py"
)


def _load_gate_module() -> Any:
    """Import the gate script as a module without polluting sys.modules."""
    spec = importlib.util.spec_from_file_location(
        "_check_dto_forbid_extra_for_test", _GATE_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GATE = _load_gate_module()


@pytest.mark.parametrize("suffix", _GATE.DTO_SUFFIXES)
def test_gate_flags_class_missing_forbid(suffix: str, tmp_path: Path) -> None:
    """For each suffix, a BaseModel subclass without forbid is flagged."""
    source = textwrap.dedent(
        f"""
        from pydantic import BaseModel, ConfigDict

        class Foo{suffix}(BaseModel):
            model_config = ConfigDict(frozen=True)
            value: int = 0
        """
    )
    target = tmp_path / "sample.py"
    target.write_text(source, encoding="utf-8")
    violations = _GATE._walk(target)
    names = [name for _, _, name in violations]
    assert names == [f"Foo{suffix}"]


@pytest.mark.parametrize("suffix", _GATE.DTO_SUFFIXES)
def test_gate_passes_class_with_forbid(suffix: str, tmp_path: Path) -> None:
    """For each suffix, a BaseModel subclass declaring forbid is not flagged."""
    source = textwrap.dedent(
        f"""
        from pydantic import BaseModel, ConfigDict

        class Foo{suffix}(BaseModel):
            model_config = ConfigDict(frozen=True, extra="forbid")
            value: int = 0
        """
    )
    target = tmp_path / "sample.py"
    target.write_text(source, encoding="utf-8")
    assert _GATE._walk(target) == []


def test_gate_ignores_non_dto_class(tmp_path: Path) -> None:
    """Classes not matching any DTO suffix are not gated."""
    source = textwrap.dedent(
        """
        from pydantic import BaseModel, ConfigDict

        class FooThing(BaseModel):
            model_config = ConfigDict(frozen=True)
            value: int = 0
        """
    )
    target = tmp_path / "sample.py"
    target.write_text(source, encoding="utf-8")
    assert _GATE._walk(target) == []


def test_gate_ignores_non_pydantic_class(tmp_path: Path) -> None:
    """A class with a DTO suffix that doesn't inherit from BaseModel is ignored."""
    source = textwrap.dedent(
        """
        class FooResponse:
            value: int = 0
        """
    )
    target = tmp_path / "sample.py"
    target.write_text(source, encoding="utf-8")
    assert _GATE._walk(target) == []


def test_gate_flags_class_with_no_model_config(tmp_path: Path) -> None:
    """A DTO without any ``model_config`` is treated as a violation."""
    source = textwrap.dedent(
        """
        from pydantic import BaseModel

        class FooResponse(BaseModel):
            value: int = 0
        """
    )
    target = tmp_path / "sample.py"
    target.write_text(source, encoding="utf-8")
    violations = _GATE._walk(target)
    assert [name for _, _, name in violations] == ["FooResponse"]


def test_gate_respects_optout_with_reason(tmp_path: Path) -> None:
    """Class line carrying a ``# lint-allow: ...`` comment is exempted."""
    source = textwrap.dedent(
        """
        from pydantic import BaseModel, ConfigDict

        class FooResponse(BaseModel):  # lint-allow: dto-forbid-extra -- legacy shape
            model_config = ConfigDict(frozen=True)
            value: int = 0
        """
    )
    target = tmp_path / "sample.py"
    target.write_text(source, encoding="utf-8")
    assert _GATE._walk(target) == []


def test_gate_rejects_optout_without_reason(tmp_path: Path) -> None:
    """A bare opt-out without a ``-- <reason>`` is not honoured."""
    source = textwrap.dedent(
        """
        from pydantic import BaseModel, ConfigDict

        class FooResponse(BaseModel):  # lint-allow: dto-forbid-extra
            model_config = ConfigDict(frozen=True)
            value: int = 0
        """
    )
    target = tmp_path / "sample.py"
    target.write_text(source, encoding="utf-8")
    violations = _GATE._walk(target)
    assert [name for _, _, name in violations] == ["FooResponse"]


def test_gate_flags_subclass_of_suffixed_base_without_forbid(tmp_path: Path) -> None:
    """A leaf DTO whose parent has a DTO suffix must repeat ``extra="forbid"``."""
    source = textwrap.dedent(
        """
        from pydantic import BaseModel, ConfigDict

        class FooResponse(BaseModel):
            model_config = ConfigDict(frozen=True, extra="forbid")
            value: int = 0

        class BarResponse(FooResponse):
            other: int = 0
        """
    )
    target = tmp_path / "sample.py"
    target.write_text(source, encoding="utf-8")
    violations = _GATE._walk(target)
    assert [name for _, _, name in violations] == ["BarResponse"]


def test_gate_recognises_generic_subscripted_base(tmp_path: Path) -> None:
    """A DTO with PEP 695 generic ``BaseModel[T]`` base is gated like ``BaseModel``."""
    source = textwrap.dedent(
        """
        from pydantic import BaseModel, ConfigDict

        class FooEnvelope[T](BaseModel):
            model_config = ConfigDict(frozen=True)
            value: T | None = None
        """
    )
    target = tmp_path / "sample.py"
    target.write_text(source, encoding="utf-8")
    violations = _GATE._walk(target)
    assert [name for _, _, name in violations] == ["FooEnvelope"]
