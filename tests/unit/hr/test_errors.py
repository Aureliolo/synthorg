"""Tests for HR domain error hierarchy."""

import pytest

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
