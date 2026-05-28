"""Unit tests for client simulation domain models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.client.models import (
    ClientFeedback,
    ClientProfile,
    ClientRequest,
    GenerationContext,
    PoolConstraints,
    RequestStatus,
    ReviewContext,
    SimulationConfig,
    SimulationMetrics,
    TaskRequirement,
    validate_request_transition,
)
from synthorg.core.enums import Complexity, Priority, TaskType

pytestmark = pytest.mark.unit


# ── RequestStatus Enum ──────────────────────────────────────────


class TestRequestStatus:
    """Tests for the RequestStatus enum."""

    def test_has_six_members(self) -> None:
        assert len(RequestStatus) == 6

    def test_values(self) -> None:
        expected = {
            "submitted",
            "triaging",
            "scoping",
            "approved",
            "task_created",
            "cancelled",
        }
        assert {s.value for s in RequestStatus} == expected

    def test_terminal_states(self) -> None:
        from synthorg.client.models import VALID_REQUEST_TRANSITIONS

        for status in (RequestStatus.TASK_CREATED, RequestStatus.CANCELLED):
            assert VALID_REQUEST_TRANSITIONS[status] == frozenset()


# ── Request Transitions ────────────────────────────────────────


class TestRequestTransitions:
    """Tests for the request status state machine."""

    @pytest.mark.parametrize(
        ("source", "target"),
        [
            (RequestStatus.SUBMITTED, RequestStatus.TRIAGING),
            (RequestStatus.SUBMITTED, RequestStatus.CANCELLED),
            (RequestStatus.TRIAGING, RequestStatus.SCOPING),
            (RequestStatus.TRIAGING, RequestStatus.CANCELLED),
            (RequestStatus.SCOPING, RequestStatus.APPROVED),
            (RequestStatus.SCOPING, RequestStatus.CANCELLED),
            (RequestStatus.APPROVED, RequestStatus.TASK_CREATED),
            (RequestStatus.APPROVED, RequestStatus.CANCELLED),
        ],
    )
    def test_valid_transition(
        self,
        source: RequestStatus,
        target: RequestStatus,
    ) -> None:
        validate_request_transition(source, target)

    @pytest.mark.parametrize(
        ("source", "target"),
        [
            (RequestStatus.SUBMITTED, RequestStatus.APPROVED),
            (RequestStatus.SUBMITTED, RequestStatus.TASK_CREATED),
            (RequestStatus.TRIAGING, RequestStatus.SUBMITTED),
            (RequestStatus.TASK_CREATED, RequestStatus.SUBMITTED),
            (RequestStatus.CANCELLED, RequestStatus.SUBMITTED),
        ],
    )
    def test_invalid_transition(
        self,
        source: RequestStatus,
        target: RequestStatus,
    ) -> None:
        with pytest.raises(ValueError, match="Invalid request transition"):
            validate_request_transition(source, target)


# ── ClientProfile ───────────────────────────────────────────────


class TestClientProfile:
    """Tests for the ClientProfile model."""

    def test_valid_profile(self) -> None:
        profile = ClientProfile(
            client_id="client-1",
            name="Test Client",
            persona="A detail-oriented QA lead",
        )
        assert profile.client_id == "client-1"
        assert profile.strictness_level == 0.5

    def test_frozen(self) -> None:
        profile = ClientProfile(
            client_id="client-1",
            name="Test Client",
            persona="A persona",
        )
        with pytest.raises(ValidationError):
            profile.client_id = "changed"  # type: ignore[misc]

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ClientProfile(
                client_id="client-1",
                name="   ",
                persona="A persona",
            )

    def test_strictness_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ClientProfile(
                client_id="client-1",
                name="Test",
                persona="A persona",
                strictness_level=1.5,
            )

    def test_expertise_domains(self) -> None:
        profile = ClientProfile(
            client_id="client-1",
            name="Test",
            persona="A persona",
            expertise_domains=("backend", "security"),
        )
        assert len(profile.expertise_domains) == 2


# ── TaskRequirement ─────────────────────────────────────────────


class TestTaskRequirement:
    """Tests for the TaskRequirement model."""

    def test_valid_requirement(self) -> None:
        req = TaskRequirement(
            title="Implement auth",
            description="Add JWT authentication to the API",
        )
        assert req.task_type == TaskType.DEVELOPMENT
        assert req.priority == Priority.MEDIUM
        assert req.estimated_complexity == Complexity.MEDIUM
        assert req.acceptance_criteria == ()

    def test_frozen(self) -> None:
        req = TaskRequirement(
            title="Test",
            description="Test desc",
        )
        with pytest.raises(ValidationError):
            req.title = "changed"  # type: ignore[misc]

    def test_with_acceptance_criteria(self) -> None:
        req = TaskRequirement(
            title="Test",
            description="Test desc",
            acceptance_criteria=("Tests pass", "Docs updated"),
        )
        assert len(req.acceptance_criteria) == 2


# ── GenerationContext ───────────────────────────────────────────


class TestGenerationContext:
    """Tests for the GenerationContext model."""

    def test_valid_context(self) -> None:
        ctx = GenerationContext(
            project_id="proj-1",
            domain="backend",
        )
        assert ctx.count == 1
        assert len(ctx.complexity_range) == 3

    def test_count_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            GenerationContext(
                project_id="proj-1",
                domain="backend",
                count=0,
            )

    def test_empty_complexity_range_rejected(self) -> None:
        with pytest.raises(ValidationError, match="complexity_range"):
            GenerationContext(
                project_id="proj-1",
                domain="backend",
                complexity_range=(),
            )


# ── ReviewContext ───────────────────────────────────────────────


class TestReviewContext:
    """Tests for the ReviewContext model."""

    def test_valid_context(self) -> None:
        ctx = ReviewContext(
            task_id="task-1",
            task_title="Test task",
            deliverable_summary="Implementation complete",
        )
        assert ctx.acceptance_criteria == ()
        assert ctx.prior_feedback == ()

    def test_blank_deliverable_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReviewContext(
                task_id="task-1",
                task_title="Test",
                deliverable_summary="   ",
            )


# ── ClientFeedback ──────────────────────────────────────────────


class TestClientFeedback:
    """Tests for the ClientFeedback model."""

    def test_accepted_feedback(self) -> None:
        feedback = ClientFeedback(
            task_id="task-1",
            client_id="client-1",
            accepted=True,
        )
        assert feedback.accepted is True
        assert feedback.reason is None
        assert feedback.feedback_id  # auto-generated

    def test_rejected_requires_reason(self) -> None:
        with pytest.raises(ValidationError, match="reason is required"):
            ClientFeedback(
                task_id="task-1",
                client_id="client-1",
                accepted=False,
            )

    def test_rejected_with_reason(self) -> None:
        feedback = ClientFeedback(
            task_id="task-1",
            client_id="client-1",
            accepted=False,
            reason="Missing error handling",
        )
        assert feedback.accepted is False
        assert feedback.reason == "Missing error handling"

    def test_with_scores(self) -> None:
        feedback = ClientFeedback(
            task_id="task-1",
            client_id="client-1",
            accepted=True,
            scores={"quality": 0.9, "completeness": 0.8},
        )
        assert feedback.scores is not None
        assert feedback.scores["quality"] == 0.9

    def test_scores_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError, match="scores"):
            ClientFeedback(
                task_id="task-1",
                client_id="client-1",
                accepted=True,
                scores={"quality": 1.5},
            )

    def test_scores_negative_rejected(self) -> None:
        with pytest.raises(ValidationError, match="scores"):
            ClientFeedback(
                task_id="task-1",
                client_id="client-1",
                accepted=True,
                scores={"quality": -0.1},
            )

    def test_with_unmet_criteria(self) -> None:
        feedback = ClientFeedback(
            task_id="task-1",
            client_id="client-1",
            accepted=False,
            reason="Incomplete",
            unmet_criteria=("Tests missing", "No docs"),
        )
        assert len(feedback.unmet_criteria) == 2

    def test_frozen(self) -> None:
        feedback = ClientFeedback(
            task_id="task-1",
            client_id="client-1",
            accepted=True,
        )
        with pytest.raises(ValidationError):
            feedback.accepted = False  # type: ignore[misc]

    def test_created_at_auto_set(self) -> None:
        before = datetime.now(UTC)
        feedback = ClientFeedback(
            task_id="task-1",
            client_id="client-1",
            accepted=True,
        )
        assert feedback.created_at >= before


# ── ClientRequest ───────────────────────────────────────────────


class TestClientRequest:
    """Tests for the ClientRequest model."""

    def _make_requirement(self) -> TaskRequirement:
        return TaskRequirement(
            title="Test task",
            description="A test task requirement",
        )

    def test_valid_request(self) -> None:
        req = ClientRequest(
            client_id="client-1",
            requirement=self._make_requirement(),
        )
        assert req.status == RequestStatus.SUBMITTED
        assert req.request_id  # auto-generated
        assert req.metadata == {}

    def test_with_status_valid(self) -> None:
        req = ClientRequest(
            client_id="client-1",
            requirement=self._make_requirement(),
        )
        updated = req.with_status(RequestStatus.TRIAGING)
        assert updated.status == RequestStatus.TRIAGING
        assert updated.client_id == req.client_id

    def test_with_status_carries_requirement_override(self) -> None:
        req = ClientRequest(
            client_id="client-1",
            requirement=self._make_requirement(),
        )
        refined = TaskRequirement(
            title="Refined title",
            description="Refined description",
        )
        updated = req.with_status(RequestStatus.TRIAGING, requirement=refined)
        assert updated.status == RequestStatus.TRIAGING
        assert updated.requirement.title == "Refined title"
        assert updated.request_id == req.request_id

    def test_with_status_invalid(self) -> None:
        req = ClientRequest(
            client_id="client-1",
            requirement=self._make_requirement(),
        )
        with pytest.raises(ValueError, match="Invalid request transition"):
            req.with_status(RequestStatus.APPROVED)

    def test_with_status_rejects_status_override(self) -> None:
        req = ClientRequest(
            client_id="client-1",
            requirement=self._make_requirement(),
        )
        with pytest.raises(ValueError, match="status override"):
            # mypy rejects ``status`` statically (not in the overrides
            # TypedDict); this asserts the runtime guard still rejects a
            # ``status`` smuggled through an untyped ``**overrides`` splat.
            req.with_status(  # type: ignore[call-arg]
                RequestStatus.TRIAGING,
                status=RequestStatus.APPROVED,
            )

    def test_frozen(self) -> None:
        req = ClientRequest(
            client_id="client-1",
            requirement=self._make_requirement(),
        )
        with pytest.raises(ValidationError):
            req.client_id = "changed"  # type: ignore[misc]


# ── PoolConstraints ─────────────────────────────────────────────


class TestPoolConstraints:
    """Tests for the PoolConstraints model."""

    def test_defaults(self) -> None:
        constraints = PoolConstraints()
        assert constraints.min_strictness == 0.0
        assert constraints.max_strictness == 1.0
        assert constraints.max_clients == 10

    def test_invalid_range(self) -> None:
        with pytest.raises(ValidationError, match="min_strictness"):
            PoolConstraints(min_strictness=0.8, max_strictness=0.2)

    def test_valid_range(self) -> None:
        constraints = PoolConstraints(
            min_strictness=0.3,
            max_strictness=0.7,
        )
        assert constraints.min_strictness == 0.3

    def test_equal_min_max_valid(self) -> None:
        constraints = PoolConstraints(
            min_strictness=0.5,
            max_strictness=0.5,
        )
        assert constraints.min_strictness == constraints.max_strictness


# ── SimulationConfig ────────────────────────────────────────────


class TestSimulationConfig:
    """Tests for the SimulationConfig model."""

    def test_valid_config(self) -> None:
        config = SimulationConfig(project_id="proj-1")
        assert config.rounds == 1
        assert config.clients_per_round == 5
        assert config.requirements_per_client == 1
        assert config.simulation_id  # auto-generated

    def test_rounds_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            SimulationConfig(project_id="proj-1", rounds=0)

    def test_frozen(self) -> None:
        config = SimulationConfig(project_id="proj-1")
        with pytest.raises(ValidationError):
            config.rounds = 5  # type: ignore[misc]


# ── SimulationMetrics ───────────────────────────────────────────


class TestSimulationMetrics:
    """Tests for the SimulationMetrics model."""

    def test_defaults(self) -> None:
        metrics = SimulationMetrics()
        assert metrics.total_requirements == 0
        assert metrics.total_tasks_created == 0
        assert metrics.tasks_accepted == 0
        assert metrics.tasks_rejected == 0
        assert metrics.tasks_reworked == 0

    def test_non_negative_constraint(self) -> None:
        with pytest.raises(ValidationError):
            SimulationMetrics(total_requirements=-1)

    def test_frozen(self) -> None:
        metrics = SimulationMetrics()
        with pytest.raises(ValidationError):
            metrics.total_requirements = 5  # type: ignore[misc]

    def test_acceptance_rate_computed(self) -> None:
        metrics = SimulationMetrics(
            total_tasks_created=10,
            tasks_accepted=7,
        )
        assert metrics.acceptance_rate == 0.7

    def test_rework_rate_computed(self) -> None:
        metrics = SimulationMetrics(
            total_tasks_created=10,
            tasks_reworked=3,
        )
        assert metrics.rework_rate == 0.3

    def test_rates_zero_when_no_tasks(self) -> None:
        metrics = SimulationMetrics()
        assert metrics.acceptance_rate == 0.0
        assert metrics.rework_rate == 0.0
