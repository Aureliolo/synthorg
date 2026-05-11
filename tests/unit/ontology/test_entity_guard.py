"""Tests for EntityAlignmentGuard."""

from unittest.mock import AsyncMock

import pytest

from synthorg.communication.delegation.entity_guard import (
    EntityAlignmentGuard,
    EntityGuardOutcome,
)
from synthorg.communication.delegation.models import DelegationRequest
from synthorg.core.enums import TaskType
from synthorg.core.task import Task
from synthorg.ontology.config import DelegationGuardConfig, GuardMode
from synthorg.persistence.ontology_protocol import OntologyEntityRepository


def _make_request() -> DelegationRequest:
    """Create a minimal delegation request."""
    task = Task(
        id="task-1",
        title="Do work",
        description="Some work",
        type=TaskType.DEVELOPMENT,
        project="proj-1",
        created_by="ceo",
    )
    return DelegationRequest(
        delegator_id="ceo",
        delegatee_id="cto",
        task=task,
    )


def _make_backend(
    manifest: dict[str, int] | None = None,
) -> AsyncMock:
    """Create a mock OntologyBackend."""
    backend = AsyncMock()
    if manifest is None:
        manifest = {"Task": 1, "AgentIdentity": 2}
    backend.get_version_manifest = AsyncMock(return_value=manifest)
    return backend


@pytest.mark.unit
class TestEntityGuardOutcome:
    """Tests for EntityGuardOutcome model."""

    def test_passed_outcome(self) -> None:
        """Passed outcome has empty message."""
        outcome = EntityGuardOutcome(passed=True)
        assert outcome.passed is True
        assert outcome.mechanism == "entity_alignment"
        assert outcome.message == ""

    def test_failed_outcome(self) -> None:
        """Failed outcome with message and versions."""
        outcome = EntityGuardOutcome(
            passed=False,
            message="stale versions",
            entity_versions={"Task": 1},
        )
        assert outcome.passed is False
        assert "stale" in outcome.message

    def test_frozen(self) -> None:
        """Model is immutable."""
        from pydantic_core import ValidationError

        outcome = EntityGuardOutcome(passed=True)
        with pytest.raises(ValidationError):
            outcome.passed = False  # type: ignore[misc]


@pytest.mark.unit
class TestEntityAlignmentGuardNone:
    """Tests for GuardMode.NONE."""

    async def test_none_mode_skips_check(self) -> None:
        """NONE mode returns passed without calling backend."""
        backend = _make_backend()
        config = DelegationGuardConfig(guard_mode=GuardMode.NONE)
        guard = EntityAlignmentGuard(ontology=backend, config=config)
        request = _make_request()

        outcome = await guard.check(request)
        assert outcome.passed is True
        assert outcome.entity_versions is None
        backend.get_version_manifest.assert_not_called()


@pytest.mark.unit
class TestEntityAlignmentGuardStamp:
    """Tests for GuardMode.STAMP."""

    async def test_stamp_returns_versions(self) -> None:
        """STAMP mode returns versions without blocking."""
        backend = _make_backend()
        config = DelegationGuardConfig(guard_mode=GuardMode.STAMP)
        guard = EntityAlignmentGuard(ontology=backend, config=config)
        request = _make_request()

        outcome = await guard.check(request)
        assert outcome.passed is True
        assert outcome.entity_versions == {"Task": 1, "AgentIdentity": 2}
        backend.get_version_manifest.assert_awaited_once()

    async def test_stamp_with_empty_manifest(self) -> None:
        """STAMP mode with empty manifest still passes."""
        backend = _make_backend(manifest={})
        config = DelegationGuardConfig(guard_mode=GuardMode.STAMP)
        guard = EntityAlignmentGuard(ontology=backend, config=config)
        request = _make_request()

        outcome = await guard.check(request)
        assert outcome.passed is True
        assert outcome.entity_versions == {}


@pytest.mark.unit
class TestEntityAlignmentGuardValidate:
    """Tests for GuardMode.VALIDATE."""

    async def test_validate_passes_and_stamps(self) -> None:
        """VALIDATE mode passes and returns versions."""
        backend = _make_backend()
        config = DelegationGuardConfig(guard_mode=GuardMode.VALIDATE)
        guard = EntityAlignmentGuard(ontology=backend, config=config)
        request = _make_request()

        outcome = await guard.check(request)
        assert outcome.passed is True
        assert outcome.entity_versions is not None

    async def test_validate_passes_with_empty_manifest(self) -> None:
        """VALIDATE mode passes even with empty manifest (logs warning)."""
        backend = _make_backend(manifest={})
        config = DelegationGuardConfig(guard_mode=GuardMode.VALIDATE)
        guard = EntityAlignmentGuard(ontology=backend, config=config)
        request = _make_request()

        outcome = await guard.check(request)
        assert outcome.passed is True

    async def test_validate_warns_on_stale_versions(self) -> None:
        """VALIDATE mode passes but detects stale versions."""
        backend = _make_backend(manifest={"Task": 3, "AgentIdentity": 2})
        config = DelegationGuardConfig(guard_mode=GuardMode.VALIDATE)
        guard = EntityAlignmentGuard(ontology=backend, config=config)
        request = _make_request()
        # Give request stale knowledge (Task at v1, but current is v3)
        request = request.model_copy(
            update={"entity_versions": {"Task": 1}},
        )

        outcome = await guard.check(request)
        assert outcome.passed is True  # VALIDATE allows through


@pytest.mark.unit
class TestEntityAlignmentGuardEnforce:
    """Tests for GuardMode.ENFORCE."""

    async def test_enforce_passes_with_manifest(self) -> None:
        """ENFORCE mode passes when entities are registered."""
        backend = _make_backend()
        config = DelegationGuardConfig(guard_mode=GuardMode.ENFORCE)
        guard = EntityAlignmentGuard(ontology=backend, config=config)
        request = _make_request()

        outcome = await guard.check(request)
        assert outcome.passed is True
        assert outcome.entity_versions is not None

    async def test_enforce_blocks_stale_versions(self) -> None:
        """ENFORCE mode rejects when request has stale entity versions."""
        backend = _make_backend(manifest={"Task": 3, "AgentIdentity": 2})
        config = DelegationGuardConfig(guard_mode=GuardMode.ENFORCE)
        guard = EntityAlignmentGuard(ontology=backend, config=config)
        request = _make_request()
        request = request.model_copy(
            update={"entity_versions": {"Task": 1}},
        )

        outcome = await guard.check(request)
        assert outcome.passed is False
        assert "stale" in outcome.message.lower() or "v1" in outcome.message

    async def test_enforce_blocks_empty_manifest(self) -> None:
        """ENFORCE mode blocks when no entities are registered."""
        backend = _make_backend(manifest={})
        config = DelegationGuardConfig(guard_mode=GuardMode.ENFORCE)
        guard = EntityAlignmentGuard(ontology=backend, config=config)
        request = _make_request()

        outcome = await guard.check(request)
        assert outcome.passed is False
        assert "no entities" in outcome.message.lower()

    async def test_enforce_fails_closed_on_backend_error(self) -> None:
        """ENFORCE mode rejects when manifest retrieval fails."""
        backend = AsyncMock(spec=OntologyEntityRepository)
        backend.get_version_manifest = AsyncMock(
            side_effect=RuntimeError("connection lost"),
        )
        config = DelegationGuardConfig(guard_mode=GuardMode.ENFORCE)
        guard = EntityAlignmentGuard(ontology=backend, config=config)
        request = _make_request()

        outcome = await guard.check(request)
        assert outcome.passed is False
        msg = outcome.message.lower()
        assert "failed" in msg or "could not" in msg


@pytest.mark.unit
class TestEntityGuardImmutability:
    """Tests for entity_versions immutability."""

    async def test_stamp_returns_deep_copy(self) -> None:
        """STAMP mode returns a copy -- mutating source doesn't affect outcome."""
        original = {"Task": 1, "Agent": 2}
        backend = _make_backend(manifest=original)
        config = DelegationGuardConfig(guard_mode=GuardMode.STAMP)
        guard = EntityAlignmentGuard(ontology=backend, config=config)
        request = _make_request()

        outcome = await guard.check(request)
        # Mutate the source dict
        original["Task"] = 999
        # Outcome should be unaffected
        assert outcome.entity_versions is not None
        assert outcome.entity_versions["Task"] == 1

        # Field reassignment is blocked by frozen model
        from pydantic_core import ValidationError

        with pytest.raises(ValidationError):
            outcome.entity_versions = {}  # type: ignore[misc]
        # Value still intact after failed reassignment
        assert outcome.entity_versions["Task"] == 1


@pytest.mark.unit
class TestEntityAlignmentGuardProperties:
    """Tests for guard properties."""

    def test_guard_mode_property(self) -> None:
        """guard_mode property returns config mode."""
        backend = _make_backend()
        config = DelegationGuardConfig(guard_mode=GuardMode.STAMP)
        guard = EntityAlignmentGuard(ontology=backend, config=config)
        assert guard.guard_mode == GuardMode.STAMP

    @pytest.mark.parametrize(
        "mode",
        [GuardMode.NONE, GuardMode.STAMP, GuardMode.VALIDATE, GuardMode.ENFORCE],
    )
    async def test_all_modes_return_outcome(
        self,
        mode: GuardMode,
    ) -> None:
        """All modes return EntityGuardOutcome."""
        backend = _make_backend()
        config = DelegationGuardConfig(guard_mode=mode)
        guard = EntityAlignmentGuard(ontology=backend, config=config)
        request = _make_request()

        outcome = await guard.check(request)
        assert isinstance(outcome, EntityGuardOutcome)
