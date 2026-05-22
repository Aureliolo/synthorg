"""AppState facade accessors for META-MCP-3 write-side services.

Provides ``has_<service>`` / ``<service>`` / ``set_<service>`` triples
for the services META-MCP-3 introduces or wires onto AppState:

- ``workflow_service`` -- workflow definition CRUD facade.
- ``workflow_execution_service`` -- workflow execution lifecycle.
- ``workflow_version_service`` -- workflow definition version reads.
- ``subworkflow_service`` -- subworkflow control plane.
- ``self_improvement_service`` -- meta-loop trigger and config readout.
- ``chief_of_staff_chat`` -- LLM-backed chat for proposal/alert/free-form
  explanations served by the ``POST /meta/chat`` endpoint.

This is the META-MCP-3 mixin; the parallel META-MCP-4 mixin lives in
``state_services_facades_mcp4.py``. Both follow the same conventions
(audit event on attach, one-shot setter, ``ServiceUnavailableError`` on
read when not wired) and exist as separate files purely so the parent
``state_services_facades.py`` stays under the project's 800-line ceiling.
"""

from typing import Any

from synthorg.engine.workflow.execution_service import (
    WorkflowExecutionService,  # noqa: TC001
)
from synthorg.engine.workflow.service import WorkflowService  # noqa: TC001
from synthorg.engine.workflow.subworkflow_service import (
    SubworkflowService,  # noqa: TC001
)
from synthorg.engine.workflow.version_service import (
    WorkflowVersionService,  # noqa: TC001
)
from synthorg.meta.charter.dispatch import CharterDispatcher  # noqa: TC001
from synthorg.meta.charter.service import (  # noqa: TC001
    CharterInterviewService,
)
from synthorg.meta.chief_of_staff.chat import ChiefOfStaffChat  # noqa: TC001
from synthorg.meta.chief_of_staff.propose import (  # noqa: TC001
    ChiefOfStaffProposer,
)
from synthorg.meta.service import SelfImprovementService  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_STATE_SERVICE_ATTACHED
from synthorg.persistence.conversational_proposal_protocol import (  # noqa: TC001
    ConversationalProposalRepository,
)

logger = get_logger(__name__)


class _MetaMcp3FacadesMixin:
    """Facade accessors for the six META-MCP-3 services.

    Covers the five original META-MCP-3 surfaces plus
    ``chief_of_staff_chat``, the LLM-backed chat backend wired in for
    ``POST /meta/chat``.
    """

    _set_once: Any

    def _require_service[T](  # pragma: no cover
        self, service: T | None, name: str
    ) -> T:
        raise NotImplementedError

    def _attach_service(
        self,
        *,
        slot: str,
        service: Any,
        name: str,
    ) -> None:
        self._set_once(slot, service, name)
        logger.info(
            API_STATE_SERVICE_ATTACHED,
            slot=slot,
            service_class=type(service).__name__,
        )

    # Slot attrs (declared on the concrete AppState; redeclared so
    # mypy narrows access through the mixin).
    _workflow_service: WorkflowService | None
    _workflow_execution_service: WorkflowExecutionService | None
    _workflow_version_service: WorkflowVersionService | None
    _subworkflow_service: SubworkflowService | None
    _self_improvement_service: SelfImprovementService | None
    _chief_of_staff_chat: ChiefOfStaffChat | None
    _chief_of_staff_proposer: ChiefOfStaffProposer | None
    _charter_service: CharterInterviewService | None
    _charter_dispatcher: CharterDispatcher | None
    _conversational_proposal_repo: ConversationalProposalRepository | None

    # ── WorkflowService ──────────────────────────────────────────

    @property
    def has_workflow_service(self) -> bool:
        """Whether the workflow definition service has been attached."""
        return self._workflow_service is not None

    @property
    def workflow_service(self) -> WorkflowService:
        """Return the attached :class:`WorkflowService`."""
        return self._require_service(
            self._workflow_service,
            "workflow_service",
        )

    def set_workflow_service(self, service: WorkflowService) -> None:
        """Attach the workflow definition service (one-shot)."""
        self._attach_service(
            slot="_workflow_service",
            service=service,
            name="workflow_service",
        )

    # ── WorkflowExecutionService ─────────────────────────────────

    @property
    def has_workflow_execution_service(self) -> bool:
        """Whether the workflow execution service has been attached."""
        return self._workflow_execution_service is not None

    @property
    def workflow_execution_service(self) -> WorkflowExecutionService:
        """Return the attached :class:`WorkflowExecutionService`."""
        return self._require_service(
            self._workflow_execution_service,
            "workflow_execution_service",
        )

    def set_workflow_execution_service(
        self,
        service: WorkflowExecutionService,
    ) -> None:
        """Attach the workflow execution service (one-shot)."""
        self._attach_service(
            slot="_workflow_execution_service",
            service=service,
            name="workflow_execution_service",
        )

    # ── WorkflowVersionService ───────────────────────────────────

    @property
    def has_workflow_version_service(self) -> bool:
        """Whether the workflow version service has been attached."""
        return self._workflow_version_service is not None

    @property
    def workflow_version_service(self) -> WorkflowVersionService:
        """Return the attached :class:`WorkflowVersionService`."""
        return self._require_service(
            self._workflow_version_service,
            "workflow_version_service",
        )

    def set_workflow_version_service(
        self,
        service: WorkflowVersionService,
    ) -> None:
        """Attach the workflow version service (one-shot)."""
        self._attach_service(
            slot="_workflow_version_service",
            service=service,
            name="workflow_version_service",
        )

    # ── SubworkflowService ───────────────────────────────────────

    @property
    def has_subworkflow_service(self) -> bool:
        """Whether the subworkflow service has been attached."""
        return self._subworkflow_service is not None

    @property
    def subworkflow_service(self) -> SubworkflowService:
        """Return the attached :class:`SubworkflowService`."""
        return self._require_service(
            self._subworkflow_service,
            "subworkflow_service",
        )

    def set_subworkflow_service(self, service: SubworkflowService) -> None:
        """Attach the subworkflow service (one-shot)."""
        self._attach_service(
            slot="_subworkflow_service",
            service=service,
            name="subworkflow_service",
        )

    # ── SelfImprovementService ───────────────────────────────────

    @property
    def has_self_improvement_service(self) -> bool:
        """Whether the self-improvement service has been attached."""
        return self._self_improvement_service is not None

    @property
    def self_improvement_service(self) -> SelfImprovementService:
        """Return the attached :class:`SelfImprovementService`."""
        return self._require_service(
            self._self_improvement_service,
            "self_improvement_service",
        )

    def set_self_improvement_service(
        self,
        service: SelfImprovementService,
    ) -> None:
        """Attach the self-improvement service (one-shot)."""
        self._attach_service(
            slot="_self_improvement_service",
            service=service,
            name="self_improvement_service",
        )

    # ── ChiefOfStaffChat ─────────────────────────────────────────

    @property
    def has_chief_of_staff_chat(self) -> bool:
        """Whether the Chief of Staff chat backend has been attached."""
        return self._chief_of_staff_chat is not None

    @property
    def chief_of_staff_chat(self) -> ChiefOfStaffChat:
        """Return the attached :class:`ChiefOfStaffChat`."""
        return self._require_service(
            self._chief_of_staff_chat,
            "chief_of_staff_chat",
        )

    def set_chief_of_staff_chat(self, service: ChiefOfStaffChat) -> None:
        """Attach the Chief of Staff chat backend (one-shot)."""
        self._attach_service(
            slot="_chief_of_staff_chat",
            service=service,
            name="chief_of_staff_chat",
        )

    # ── ChiefOfStaffProposer ─────────────────────────────────────

    @property
    def has_chief_of_staff_proposer(self) -> bool:
        """Whether the clarify-and-propose backend has been attached."""
        return self._chief_of_staff_proposer is not None

    @property
    def chief_of_staff_proposer(self) -> ChiefOfStaffProposer:
        """Return the attached :class:`ChiefOfStaffProposer`."""
        return self._require_service(
            self._chief_of_staff_proposer,
            "chief_of_staff_proposer",
        )

    def set_chief_of_staff_proposer(self, service: ChiefOfStaffProposer) -> None:
        """Attach the clarify-and-propose backend (one-shot)."""
        self._attach_service(
            slot="_chief_of_staff_proposer",
            service=service,
            name="chief_of_staff_proposer",
        )

    # ── CharterInterviewService ──────────────────────────────────

    @property
    def has_charter_service(self) -> bool:
        """Whether the charter-interview backend has been attached."""
        return self._charter_service is not None

    @property
    def charter_service(self) -> CharterInterviewService:
        """Return the attached :class:`CharterInterviewService`."""
        return self._require_service(
            self._charter_service,
            "charter_service",
        )

    def set_charter_service(self, service: CharterInterviewService) -> None:
        """Attach the charter-interview backend (one-shot)."""
        self._attach_service(
            slot="_charter_service",
            service=service,
            name="charter_service",
        )

    # ── CharterDispatcher ────────────────────────────────────────

    @property
    def has_charter_dispatcher(self) -> bool:
        """Whether the charter approval dispatcher has been attached."""
        return self._charter_dispatcher is not None

    @property
    def charter_dispatcher(self) -> CharterDispatcher:
        """Return the attached :class:`CharterDispatcher`."""
        return self._require_service(
            self._charter_dispatcher,
            "charter_dispatcher",
        )

    def set_charter_dispatcher(self, service: CharterDispatcher) -> None:
        """Attach the charter approval dispatcher (one-shot)."""
        self._attach_service(
            slot="_charter_dispatcher",
            service=service,
            name="charter_dispatcher",
        )

    # ── ConversationalProposalRepository ──────────────────────────

    @property
    def has_conversational_proposal_repo(self) -> bool:
        """Whether the conversational proposal repo has been attached."""
        return self._conversational_proposal_repo is not None

    @property
    def conversational_proposal_repo(
        self,
    ) -> ConversationalProposalRepository:
        """Return the attached ``ConversationalProposalRepository``."""
        return self._require_service(
            self._conversational_proposal_repo,
            "conversational_proposal_repo",
        )

    def set_conversational_proposal_repo(
        self, repo: ConversationalProposalRepository
    ) -> None:
        """Attach the conversational proposal repo (one-shot)."""
        self._attach_service(
            slot="_conversational_proposal_repo",
            service=repo,
            name="conversational_proposal_repo",
        )


__all__ = ["_MetaMcp3FacadesMixin"]
