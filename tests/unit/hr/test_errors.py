"""Tests for HR domain error hierarchy."""

import pytest

from synthorg.core.error_taxonomy import ErrorCode
from synthorg.hr.errors import (
    AgentAlreadyRegisteredError,
    AgentNotFoundError,
    AgentRegistryError,
    FiringError,
    HiringApprovalRequiredError,
    HiringError,
    HiringRejectedError,
    HRError,
    InsufficientDataError,
    InvalidCandidateError,
    MemoryArchivalError,
    OffboardingError,
    OnboardingError,
    PerformanceError,
    PromotionApprovalRequiredError,
    PromotionCooldownError,
    PruningUnrestartableError,
    TaskReassignmentError,
)


@pytest.mark.unit
class TestErrorHierarchy:
    """All HR errors inherit from HRError."""

    @pytest.mark.parametrize(
        "error_cls",
        [
            HiringError,
            HiringApprovalRequiredError,
            HiringRejectedError,
            InvalidCandidateError,
            FiringError,
            OffboardingError,
            TaskReassignmentError,
            MemoryArchivalError,
            OnboardingError,
            AgentRegistryError,
            AgentNotFoundError,
            AgentAlreadyRegisteredError,
            PerformanceError,
            InsufficientDataError,
        ],
    )
    def test_inherits_from_hr_error(self, error_cls: type[HRError]) -> None:
        assert issubclass(error_cls, HRError)
        err = error_cls("test message")
        assert isinstance(err, HRError)
        assert isinstance(err, Exception)
        assert str(err) == "test message"

    def test_hiring_subhierarchy(self) -> None:
        assert issubclass(HiringApprovalRequiredError, HiringError)
        assert issubclass(HiringRejectedError, HiringError)
        assert issubclass(InvalidCandidateError, HiringError)

    def test_offboarding_subhierarchy(self) -> None:
        assert issubclass(TaskReassignmentError, OffboardingError)
        assert issubclass(MemoryArchivalError, OffboardingError)

    def test_registry_subhierarchy(self) -> None:
        assert issubclass(AgentNotFoundError, AgentRegistryError)
        assert issubclass(AgentAlreadyRegisteredError, AgentRegistryError)

    def test_performance_subhierarchy(self) -> None:
        assert issubclass(InsufficientDataError, PerformanceError)


@pytest.mark.unit
class TestErrorCodes:
    """Audit 34: each HR conflict type carries a discriminating ErrorCode
    rather than the generic ``RESOURCE_CONFLICT`` so clients can branch."""

    @pytest.mark.parametrize(
        ("error_cls", "expected_code"),
        [
            (HiringApprovalRequiredError, ErrorCode.HIRING_APPROVAL_REQUIRED),
            (HiringRejectedError, ErrorCode.HIRING_REJECTED),
            (AgentAlreadyRegisteredError, ErrorCode.AGENT_ALREADY_REGISTERED),
            (PromotionCooldownError, ErrorCode.PROMOTION_COOLDOWN_ACTIVE),
            (PromotionApprovalRequiredError, ErrorCode.PROMOTION_APPROVAL_REQUIRED),
            (PruningUnrestartableError, ErrorCode.PRUNING_UNRESTARTABLE),
        ],
    )
    def test_conflict_error_carries_dedicated_code(
        self, error_cls: type[HRError], expected_code: ErrorCode
    ) -> None:
        assert error_cls.error_code == expected_code
        assert error_cls.status_code == 409
