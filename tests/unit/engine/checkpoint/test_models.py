"""Tests for checkpoint and heartbeat Pydantic models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.engine.checkpoint.models import (
    Checkpoint,
    CheckpointConfig,
    Heartbeat,
)

pytestmark = pytest.mark.timeout(30)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_checkpoint(**overrides: object) -> Checkpoint:
    """Build a Checkpoint with sensible defaults."""
    defaults: dict[str, object] = {
        "execution_id": "exec-001",
        "agent_id": "agent-001",
        "task_id": "task-001",
        "turn_number": 1,
        "context_json": '{"state": "running"}',
    }
    defaults.update(overrides)
    return Checkpoint(**defaults)  # type: ignore[arg-type]


def _make_heartbeat(**overrides: object) -> Heartbeat:
    """Build a Heartbeat with sensible defaults."""
    defaults: dict[str, object] = {
        "execution_id": "exec-001",
        "agent_id": "agent-001",
        "task_id": "task-001",
        "last_heartbeat_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return Heartbeat(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Checkpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckpointCreation:
    """Checkpoint model creation and defaults."""

    def test_auto_generates_id(self) -> None:
        cp = _make_checkpoint()
        assert cp.id
        assert len(cp.id) > 0

    def test_auto_generates_unique_ids(self) -> None:
        cp1 = _make_checkpoint()
        cp2 = _make_checkpoint()
        assert cp1.id != cp2.id

    def test_auto_generates_created_at(self) -> None:
        before = datetime.now(UTC)
        cp = _make_checkpoint()
        after = datetime.now(UTC)
        assert before <= cp.created_at <= after

    def test_explicit_id_preserved(self) -> None:
        cp = _make_checkpoint(id="custom-id")
        assert cp.id == "custom-id"

    def test_all_fields_set(self) -> None:
        cp = _make_checkpoint(
            execution_id="exec-x",
            agent_id="agent-x",
            task_id="task-x",
            turn_number=5,
            context_json='{"key": "val"}',
        )
        assert cp.execution_id == "exec-x"
        assert cp.agent_id == "agent-x"
        assert cp.task_id == "task-x"
        assert cp.turn_number == 5
        assert cp.context_json == '{"key": "val"}'


@pytest.mark.unit
class TestCheckpointFrozen:
    """Checkpoint model is immutable."""

    def test_cannot_mutate_field(self) -> None:
        cp = _make_checkpoint()
        with pytest.raises(ValidationError, match="frozen"):
            cp.turn_number = 99  # type: ignore[misc]

    def test_cannot_mutate_id(self) -> None:
        cp = _make_checkpoint()
        with pytest.raises(ValidationError, match="frozen"):
            cp.id = "changed"  # type: ignore[misc]


@pytest.mark.unit
class TestCheckpointContextJsonValidation:
    """context_json must be valid JSON."""

    def test_valid_json_passes(self) -> None:
        cp = _make_checkpoint(context_json='{"a": 1, "b": [2, 3]}')
        assert cp.context_json == '{"a": 1, "b": [2, 3]}'

    def test_empty_object_passes(self) -> None:
        cp = _make_checkpoint(context_json="{}")
        assert cp.context_json == "{}"

    def test_json_array_rejected(self) -> None:
        with pytest.raises(ValidationError, match="JSON object"):
            _make_checkpoint(context_json="[1, 2, 3]")

    def test_json_primitive_rejected(self) -> None:
        with pytest.raises(ValidationError, match="JSON object"):
            _make_checkpoint(context_json='"hello"')

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ValidationError, match="context_json must be valid JSON"):
            _make_checkpoint(context_json="{bad json")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValidationError, match="context_json must be valid JSON"):
            _make_checkpoint(context_json="")

    def test_non_json_string_raises(self) -> None:
        with pytest.raises(ValidationError, match="context_json must be valid JSON"):
            _make_checkpoint(context_json="not json at all")


@pytest.mark.unit
class TestCheckpointTurnNumberConstraint:
    """turn_number must be >= 0."""

    def test_zero_is_valid(self) -> None:
        cp = _make_checkpoint(turn_number=0)
        assert cp.turn_number == 0

    def test_positive_is_valid(self) -> None:
        cp = _make_checkpoint(turn_number=42)
        assert cp.turn_number == 42

    def test_negative_raises(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            _make_checkpoint(turn_number=-1)


@pytest.mark.unit
class TestCheckpointBlankFieldRejection:
    """NotBlankStr fields reject blank strings."""

    @pytest.mark.parametrize(
        "field",
        ["execution_id", "agent_id", "task_id"],
    )
    def test_blank_string_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError):
            _make_checkpoint(**{field: ""})

    @pytest.mark.parametrize(
        "field",
        ["execution_id", "agent_id", "task_id"],
    )
    def test_whitespace_only_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError):
            _make_checkpoint(**{field: "   "})


# ---------------------------------------------------------------------------
# Heartbeat tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHeartbeatCreation:
    """Heartbeat model creation."""

    def test_all_fields_set(self) -> None:
        now = datetime.now(UTC)
        hb = _make_heartbeat(
            execution_id="exec-hb",
            agent_id="agent-hb",
            task_id="task-hb",
            last_heartbeat_at=now,
        )
        assert hb.execution_id == "exec-hb"
        assert hb.agent_id == "agent-hb"
        assert hb.task_id == "task-hb"
        assert hb.last_heartbeat_at == now


@pytest.mark.unit
class TestHeartbeatFrozen:
    """Heartbeat model is immutable."""

    def test_cannot_mutate_field(self) -> None:
        hb = _make_heartbeat()
        with pytest.raises(ValidationError, match="frozen"):
            hb.execution_id = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CheckpointConfig tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckpointConfigDefaults:
    """CheckpointConfig default values."""

    def test_defaults(self) -> None:
        cfg = CheckpointConfig()
        assert cfg.persist_every_n_turns == 1
        assert cfg.heartbeat_interval_seconds == 30.0
        assert cfg.max_resume_attempts == 2


@pytest.mark.unit
class TestCheckpointConfigCustom:
    """CheckpointConfig with custom values."""

    def test_custom_values(self) -> None:
        cfg = CheckpointConfig(
            persist_every_n_turns=5,
            heartbeat_interval_seconds=60.0,
            max_resume_attempts=10,
        )
        assert cfg.persist_every_n_turns == 5
        assert cfg.heartbeat_interval_seconds == 60.0
        assert cfg.max_resume_attempts == 10

    def test_frozen(self) -> None:
        cfg = CheckpointConfig()
        with pytest.raises(ValidationError, match="frozen"):
            cfg.persist_every_n_turns = 99  # type: ignore[misc]


@pytest.mark.unit
class TestCheckpointConfigValidation:
    """CheckpointConfig field constraints."""

    def test_persist_every_n_turns_must_be_positive(self) -> None:
        with pytest.raises(ValidationError, match="greater than 0"):
            CheckpointConfig(persist_every_n_turns=0)

    def test_persist_every_n_turns_negative_rejected(self) -> None:
        with pytest.raises(ValidationError, match="greater than 0"):
            CheckpointConfig(persist_every_n_turns=-1)

    def test_heartbeat_interval_must_be_positive(self) -> None:
        with pytest.raises(ValidationError, match="greater than 0"):
            CheckpointConfig(heartbeat_interval_seconds=0.0)

    def test_heartbeat_interval_negative_rejected(self) -> None:
        with pytest.raises(ValidationError, match="greater than 0"):
            CheckpointConfig(heartbeat_interval_seconds=-1.0)

    def test_max_resume_attempts_zero_valid(self) -> None:
        cfg = CheckpointConfig(max_resume_attempts=0)
        assert cfg.max_resume_attempts == 0

    def test_max_resume_attempts_negative_rejected(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            CheckpointConfig(max_resume_attempts=-1)
