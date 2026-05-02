"""Every request DTO must reject unknown fields (``extra="forbid"``).

Without ``extra="forbid"`` a request DTO silently accepts unknown
payload keys, which masks client typos and lets fabricated capability
flags slip through to handler logic.  The
``scripts/check_request_dto_forbid_extra.py`` lint gate enforces the
convention statically; this test asserts the runtime behaviour for
every ``*Request`` DTO under ``src/synthorg/api/`` (47 in total).

The bare-extra-key probe uses an empty otherwise-invalid payload on
purpose: Pydantic still records the ``extra_forbidden`` error alongside
any required-field misses, so the assertion is robust to required-field
changes in the surrounding DTO.
"""

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from synthorg.api.auth.controller_dtos import (
    ChangePasswordRequest,
    LoginRequest,
    SetupRequest,
)
from synthorg.api.controllers.autonomy import AutonomyLevelRequest
from synthorg.api.controllers.collaboration import SetOverrideRequest
from synthorg.api.controllers.custom_rules import (
    CreateCustomRuleRequest,
    PreviewRuleRequest,
    UpdateCustomRuleRequest,
)
from synthorg.api.controllers.escalations import (
    CancelEscalationRequest,
    SubmitDecisionRequest,
)
from synthorg.api.controllers.events import ResumeInterruptRequest
from synthorg.api.controllers.meetings import TriggerMeetingRequest
from synthorg.api.controllers.meta import ChatRequest
from synthorg.api.controllers.quality import SetQualityOverrideRequest
from synthorg.api.controllers.reports import GenerateReportRequest
from synthorg.api.controllers.scaling import (
    PriorityUpdateRequest,
    StrategyUpdateRequest,
)
from synthorg.api.controllers.settings import (
    SecurityConfigImportRequest,
    UpdateSettingRequest,
)

# Alias to avoid pytest's ``Test``-prefix auto-collection (pytest tries
# to instantiate any module-level ``Test*`` symbol as a test class).
from synthorg.api.controllers.settings import (
    TestSinkConfigRequest as _SinkConfigRequest,
)
from synthorg.api.controllers.subworkflows import CreateSubworkflowRequest
from synthorg.api.controllers.users import (
    CreateUserRequest,
    GrantOrgRoleRequest,
    UpdateUserRoleRequest,
)
from synthorg.api.dto import (
    ApproveRequest,
    CancelTaskRequest,
    CoordinateTaskRequest,
    CreateApprovalRequest,
    CreateArtifactRequest,
    CreateProjectRequest,
    CreateTaskRequest,
    RejectRequest,
    RollbackAgentIdentityRequest,
    TransitionTaskRequest,
    UpdateTaskRequest,
)
from synthorg.api.dto_discovery import (
    AddAllowlistEntryRequest,
    RemoveAllowlistEntryRequest,
)
from synthorg.api.dto_ontology import (
    CreateEntityRequest,
    UpdateEntityRequest,
)
from synthorg.api.dto_training import (
    CreateTrainingPlanRequest,
    UpdateTrainingOverridesRequest,
)
from synthorg.api.dto_workflow import (
    ActivateWorkflowRequest,
    CreateFromBlueprintRequest,
    CreateWorkflowDefinitionRequest,
    RollbackWorkflowRequest,
    UpdateWorkflowDefinitionRequest,
    WorkflowIODeclarationRequest,
)

pytestmark = pytest.mark.unit

# Every ``*Request`` Pydantic DTO under ``src/synthorg/api/`` -- the
# audit-flagged 23 plus the 24 inline siblings the lint gate caught.
# Each MUST have ``ConfigDict(..., extra="forbid")``.
REQUEST_DTOS: tuple[type[BaseModel], ...] = (
    # auth/controller_dtos.py (3)
    SetupRequest,
    LoginRequest,
    ChangePasswordRequest,
    # controllers/* inline DTOs (21)
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
    # dto.py (11)
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
    # dto_discovery.py (2)
    AddAllowlistEntryRequest,
    RemoveAllowlistEntryRequest,
    # dto_ontology.py (2)
    CreateEntityRequest,
    UpdateEntityRequest,
    # dto_training.py (2)
    CreateTrainingPlanRequest,
    UpdateTrainingOverridesRequest,
    # dto_workflow.py (6)
    WorkflowIODeclarationRequest,
    CreateWorkflowDefinitionRequest,
    UpdateWorkflowDefinitionRequest,
    ActivateWorkflowRequest,
    CreateFromBlueprintRequest,
    RollbackWorkflowRequest,
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
