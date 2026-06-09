"""Shared service factory for the workflow controllers."""

from litestar.datastructures import State

from synthorg.api.controllers._workflow_builders import wf_versioning
from synthorg.engine.workflow.service import WorkflowService
from synthorg.persistence.state import persistence_of


def _service(state: State) -> WorkflowService:
    """Build the per-request :class:`WorkflowService`.

    Wires in the :class:`VersioningService` for workflow definitions so
    create/update paths persist a best-effort version snapshot in the
    same service call -- controllers no longer orchestrate the two
    writes by hand.

    Returns:
        ``WorkflowService`` instance.
    """
    return WorkflowService(
        definition_repo=persistence_of(state.app_state).workflow_definitions,
        version_repo=persistence_of(state.app_state).workflow_versions,
        versioning_service=wf_versioning(state),
    )
