"""Unit tests for the ``ProjectEnvironment`` domain model."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.enums import EnvironmentType
from synthorg.core.project_environment import ProjectEnvironment
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit


def _environment(**overrides: object) -> ProjectEnvironment:
    base: dict[str, object] = {
        "project_id": NotBlankStr("proj-1"),
        "environment_type": EnvironmentType.MANIFEST,
        "declaration_hash": NotBlankStr("a" * 64),
        "provisioned_at": datetime(2026, 5, 21, tzinfo=UTC),
        "updated_at": datetime(2026, 5, 21, tzinfo=UTC),
    }
    base.update(overrides)
    return ProjectEnvironment(**base)  # type: ignore[arg-type]


class TestProjectEnvironmentModel:
    def test_minimal_construction_defaults(self) -> None:
        env = _environment()
        assert env.project_id == "proj-1"
        assert env.environment_type is EnvironmentType.MANIFEST
        assert env.image_ref is None

    def test_is_frozen(self) -> None:
        env = _environment()
        with pytest.raises(ValidationError):
            env.project_id = NotBlankStr("other")  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            _environment(unexpected="x")

    def test_environment_type_round_trips(self) -> None:
        env = _environment(environment_type=EnvironmentType.DEVCONTAINER)
        dumped = env.model_dump()
        assert dumped["environment_type"] == "devcontainer"
        assert ProjectEnvironment.model_validate(dumped).environment_type is (
            EnvironmentType.DEVCONTAINER
        )

    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _environment(provisioned_at=datetime(2026, 5, 21))  # noqa: DTZ001

    def test_blank_project_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _environment(project_id="   ")

    def test_blank_declaration_hash_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _environment(declaration_hash="   ")

    def test_image_ref_optional_set(self) -> None:
        env = _environment(
            environment_type=EnvironmentType.DEVCONTAINER,
            image_ref=NotBlankStr("synthorg-project-proj-1:abc123"),
        )
        assert env.image_ref == "synthorg-project-proj-1:abc123"
