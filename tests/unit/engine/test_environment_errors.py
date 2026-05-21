"""Unit tests for the per-project environment error hierarchy."""

import pytest

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.engine.errors import (
    EngineError,
    EnvironmentBackendUnavailableError,
    EnvironmentConfigError,
    EnvironmentDockerBuildError,
    EnvironmentProvisionError,
    ProjectEnvironmentError,
)

pytestmark = pytest.mark.unit


class TestEnvironmentErrors:
    def test_base_inherits_engine_and_domain(self) -> None:
        assert issubclass(ProjectEnvironmentError, EngineError)
        assert issubclass(ProjectEnvironmentError, DomainError)

    def test_base_is_internal(self) -> None:
        assert ProjectEnvironmentError.error_code is ErrorCode.ENVIRONMENT_ERROR
        assert ProjectEnvironmentError.error_category is ErrorCategory.INTERNAL

    def test_config_error_is_validation(self) -> None:
        assert issubclass(EnvironmentConfigError, ProjectEnvironmentError)
        assert EnvironmentConfigError.status_code == 422
        assert EnvironmentConfigError.error_category is ErrorCategory.VALIDATION

    def test_provision_error_code(self) -> None:
        assert issubclass(EnvironmentProvisionError, ProjectEnvironmentError)
        assert (
            EnvironmentProvisionError.error_code
            is ErrorCode.ENVIRONMENT_PROVISION_FAILED
        )

    def test_docker_build_error_is_provision_subtype(self) -> None:
        assert issubclass(EnvironmentDockerBuildError, EnvironmentProvisionError)
        assert (
            EnvironmentDockerBuildError.error_code
            is ErrorCode.ENVIRONMENT_DOCKER_BUILD_FAILED
        )

    def test_backend_unavailable_is_conflict(self) -> None:
        assert issubclass(EnvironmentBackendUnavailableError, ProjectEnvironmentError)
        assert EnvironmentBackendUnavailableError.status_code == 409
        assert (
            EnvironmentBackendUnavailableError.error_category is ErrorCategory.CONFLICT
        )
        assert (
            EnvironmentBackendUnavailableError.error_code
            is ErrorCode.ENVIRONMENT_BACKEND_UNAVAILABLE
        )

    def test_does_not_shadow_builtin_environment_error(self) -> None:
        # The base is named ProjectEnvironmentError precisely so the
        # built-in EnvironmentError (OSError alias) is not shadowed.
        assert ProjectEnvironmentError is not OSError
        assert not issubclass(ProjectEnvironmentError, OSError)
