# module-kind: adapter
"""Workflow execution service -- activate definitions into tasks.

Bridges design-time ``WorkflowDefinition`` blueprints and
runtime ``Task`` instances by walking the graph in topological
order, creating concrete tasks for TASK nodes, and wiring
upstream task dependencies from the graph edges.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import uuid4

from synthorg.core.pagination import DEFAULT_LIST_LIMIT
from synthorg.core.registry import (
    StrategyFactoryNotFoundError,
)
from synthorg.engine.errors import (
    SubworkflowDepthExceededError,
    WorkflowDefinitionInvalidError,
    WorkflowExecutionError,
    WorkflowExecutionNotFoundError,
)
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskStateChanged
from synthorg.engine.workflow import execution_lifecycle as lifecycle
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowNode,
)
from synthorg.engine.workflow.enums import (
    WorkflowEdgeType,
    WorkflowExecutionStatus,
    WorkflowNodeExecutionStatus,
    WorkflowNodeType,
)
from synthorg.engine.workflow.execution_activation_helpers import (
    process_task_node,
)
from synthorg.engine.workflow.execution_models import (
    ExecutionFrame,
    WorkflowExecution,
    WorkflowNodeExecution,
)
from synthorg.engine.workflow.execution_node_dispatch import (
    _NODE_HANDLER_REGISTRY,
    _NodeDispatchContext,
)
from synthorg.engine.workflow.execution_walk_state import (
    WalkState,
    collect_terminal_task_ids,
    qualify_id,
)
from synthorg.engine.workflow.graph_utils import (
    build_adjacency_maps,
    topological_sort,
)
from synthorg.engine.workflow.subworkflow_binding import (
    project_output_bindings,
    resolve_input_bindings,
)
from synthorg.engine.workflow.subworkflow_registry import (
    SubworkflowRegistry,
)
from synthorg.engine.workflow.validation import validate_workflow
from synthorg.observability import get_logger
from synthorg.observability.events.workflow_execution import (
    WORKFLOW_EXEC_ACTIVATED,
    WORKFLOW_EXEC_INVALID_DEFINITION,
    WORKFLOW_EXEC_NODE_COMPLETED,
    WORKFLOW_EXEC_NODE_SKIPPED,
    WORKFLOW_EXEC_NOT_FOUND,
    WORKFLOW_EXEC_STATUS_TRANSITIONED,
    WORKFLOW_EXEC_SUBWORKFLOW_CONFIG_INVALID,
    WORKFLOW_EXEC_SUBWORKFLOW_DEPTH_EXCEEDED,
    WORKFLOW_EXEC_SUBWORKFLOW_FRAME_POPPED,
    WORKFLOW_EXEC_SUBWORKFLOW_FRAME_PUSHED,
    WORKFLOW_EXEC_SUBWORKFLOW_NODE_COMPLETED,
)

if TYPE_CHECKING:
    from synthorg.persistence.workflow_definition_protocol import (
        WorkflowDefinitionRepository,
    )
    from synthorg.persistence.workflow_execution_protocol import (
        WorkflowExecutionRepository,
    )

logger = get_logger(__name__)


class WorkflowExecutionService:
    """Activates workflow definitions into concrete task instances.

    Walks the definition graph in topological order, creates
    ``Task`` instances for TASK nodes via the ``TaskEngine``,
    and tracks per-node execution state.

    Args:
        definition_repo: Repository for reading workflow definitions.
        execution_repo: Repository for persisting execution state.
        task_engine: Engine for creating concrete tasks.
    """

    def __init__(
        self,
        *,
        definition_repo: WorkflowDefinitionRepository,
        execution_repo: WorkflowExecutionRepository,
        task_engine: TaskEngine,
        max_subworkflow_depth: int,
        subworkflow_registry: SubworkflowRegistry | None = None,
    ) -> None:
        self._definition_repo = definition_repo
        self._execution_repo = execution_repo
        self._task_engine = task_engine
        self._subworkflow_registry = subworkflow_registry
        self._max_subworkflow_depth = max_subworkflow_depth

    async def activate(
        self,
        definition_id: str,
        *,
        project: str,
        activated_by: str,
        context: Mapping[str, object] | None = None,
    ) -> WorkflowExecution:
        """Activate a workflow definition, creating task instances.

        Args:
            definition_id: ID of the workflow definition to activate.
            project: Project ID for all created tasks.
            activated_by: Identity of the user triggering activation.
            context: Runtime context for condition evaluation.

        Returns:
            The created ``WorkflowExecution`` in RUNNING status.

        Raises:
            WorkflowExecutionNotFoundError: If the definition is
                not found.
            WorkflowDefinitionInvalidError: If the definition fails
                validation.
            WorkflowConditionEvalError: If a condition expression
                cannot be evaluated.
            PersistenceError: If the execution cannot be persisted.
            WorkflowExecutionError: If an unhandled node type is
                encountered.
        """
        ctx = dict(context) if context else {}

        # 1. Load and validate
        definition = await self._load_and_validate(definition_id)

        # 2. Walk nodes in topological order, starting from the root frame.
        execution_id = uuid4()
        now = datetime.now(UTC)
        state = WalkState()
        root_frame = ExecutionFrame(
            workflow_id=str(definition.id),
            workflow_version=definition.version,
            variables=ctx,
            parent_frame=None,
            depth=0,
        )
        await self._walk_frame(
            definition=definition,
            frame=root_frame,
            qualifier_prefix="",
            state=state,
            execution_id=str(execution_id),
            project=project,
            activated_by=activated_by,
        )

        # 3. Build and persist execution
        # If no tasks were created, the workflow is immediately complete
        if state.node_task_ids:
            status = WorkflowExecutionStatus.RUNNING
            completed_at = None
        else:
            status = WorkflowExecutionStatus.COMPLETED
            completed_at = now

        execution = WorkflowExecution(
            id=execution_id,
            definition_id=str(definition.id),
            definition_revision=definition.revision,
            status=status,
            node_executions=tuple(
                state.node_exec_map[key] for key in state.ordered_keys
            ),
            activated_by=activated_by,
            project=project,
            created_at=now,
            updated_at=now,
            completed_at=completed_at,
        )
        await self._execution_repo.save(execution)

        logger.info(
            WORKFLOW_EXEC_ACTIVATED,
            execution_id=str(execution_id),
            definition_id=str(definition.id),
            task_count=len(state.node_task_ids),
        )
        logger.info(
            WORKFLOW_EXEC_STATUS_TRANSITIONED,
            execution_id=str(execution_id),
            definition_id=str(definition.id),
            from_status=None,
            to_status=status.value,
        )

        return execution

    async def _load_and_validate(
        self,
        definition_id: str,
    ) -> WorkflowDefinition:
        """Load a workflow definition and validate it.

        Returns:
            The validated :class:`WorkflowDefinition` ready for
            execution walking.

        Raises:
            WorkflowExecutionNotFoundError: If not found.
            WorkflowDefinitionInvalidError: If invalid.
        """
        definition = await self._definition_repo.get(definition_id)
        if definition is None:
            logger.warning(
                WORKFLOW_EXEC_NOT_FOUND,
                definition_id=definition_id,
            )
            msg = f"Workflow definition {definition_id!r} not found"
            raise WorkflowExecutionNotFoundError(msg)

        validation = validate_workflow(definition)
        if not validation.valid:
            error_msgs = "; ".join(e.message for e in validation.errors)
            logger.warning(
                WORKFLOW_EXEC_INVALID_DEFINITION,
                definition_id=definition_id,
                errors=error_msgs,
            )
            msg = f"Workflow definition {definition_id!r} is invalid: {error_msgs}"
            raise WorkflowDefinitionInvalidError(msg)

        return definition

    async def _walk_frame(  # noqa: PLR0913
        self,
        *,
        definition: WorkflowDefinition,
        frame: ExecutionFrame,
        qualifier_prefix: str,
        state: WalkState,
        execution_id: str,
        project: str,
        activated_by: str,
    ) -> dict[str, object]:
        """Walk one workflow graph inside a scoped execution frame.

        Walks *definition* in topological order, mutating *state*
        in place.  On hitting a SUBWORKFLOW node, recursively walks
        the referenced child definition inside a new frame whose
        variables are scoped to the declared inputs only.

        Node execution entries are stored under qualified keys so
        that nested graphs never collide with the parent.  The root
        frame uses no prefix and keeps existing unqualified IDs.

        Returns:
            The frame's final mutable variable map.  Callers (parent
            frames invoking a subworkflow) use this to project outputs
            back into their own scope.
        """
        adjacency, reverse_adj, outgoing = build_adjacency_maps(definition)
        node_map = {n.id: n for n in definition.nodes}
        sorted_ids = topological_sort(
            [n.id for n in definition.nodes],
            adjacency,
        )

        # Frame-local task-ID map: upstream lookups must not cross frames.
        # SUBWORKFLOW nodes may store a tuple of terminal task IDs.
        frame_node_task_ids: dict[str, str | tuple[str, ...]] = {}
        skipped_nodes: set[str] = set()
        pending_assignments: dict[str, str] = {}
        # The child graph's condition evaluator sees only the frame's
        # variable map -- parent keys cannot leak in.  This dict is
        # mutated in place as subworkflow outputs project values
        # back into the caller's scope during the walk.
        frame_ctx: dict[str, object] = dict(frame.variables)

        for nid in sorted_ids:
            qualified = qualify_id(qualifier_prefix, nid)
            node = node_map[nid]

            if nid in skipped_nodes:
                self._record_node(
                    state,
                    qualified,
                    WorkflowNodeExecution(
                        node_id=qualified,
                        node_type=node.type,
                        status=WorkflowNodeExecutionStatus.SKIPPED,
                        skipped_reason="Conditional branch not taken",
                    ),
                )
                logger.debug(
                    WORKFLOW_EXEC_NODE_SKIPPED,
                    execution_id=execution_id,
                    node_id=qualified,
                )
                continue

            node_execution = await self._process_node_in_frame(
                nid=nid,
                qualified_id=qualified,
                node=node,
                adjacency=adjacency,
                reverse_adj=reverse_adj,
                outgoing=outgoing,
                frame=frame,
                frame_ctx=frame_ctx,
                execution_id=execution_id,
                project=project,
                activated_by=activated_by,
                node_map=node_map,
                frame_node_task_ids=frame_node_task_ids,
                qualifier_prefix=qualifier_prefix,
                state=state,
                skipped_nodes=skipped_nodes,
                pending_assignments=pending_assignments,
            )
            self._record_node(state, qualified, node_execution)

        return frame_ctx

    def _record_node(
        self,
        state: WalkState,
        qualified_id: str,
        node_execution: WorkflowNodeExecution,
    ) -> None:
        """Store a processed node execution in *state* preserving order."""
        if qualified_id not in state.node_exec_map:
            state.ordered_keys.append(qualified_id)
        state.node_exec_map[qualified_id] = node_execution
        if node_execution.task_id is not None:
            state.node_task_ids[qualified_id] = node_execution.task_id

    async def _process_node_in_frame(  # noqa: PLR0913
        self,
        *,
        nid: str,
        qualified_id: str,
        node: WorkflowNode,
        adjacency: dict[str, list[str]],
        reverse_adj: dict[str, list[str]],
        outgoing: dict[str, list[tuple[str, WorkflowEdgeType]]],
        frame: ExecutionFrame,
        frame_ctx: dict[str, object],
        execution_id: str,
        project: str,
        activated_by: str,
        node_map: dict[str, WorkflowNode],
        frame_node_task_ids: dict[str, str | tuple[str, ...]],
        qualifier_prefix: str,
        state: WalkState,
        skipped_nodes: set[str],
        pending_assignments: dict[str, str],
    ) -> WorkflowNodeExecution:
        """Dispatch a single node to its registered handler.

        The per-:class:`WorkflowNodeType` if-cascade was replaced by a
        registry dispatch (ADR-0002). An unregistered node type raises
        :class:`StrategyFactoryNotFoundError` at lookup, which is
        translated to the historical :class:`WorkflowExecutionError`
        contract (same message + error log) so callers are unaffected.

        Returns:
            The :class:`WorkflowNodeExecution` produced by the
            registered handler for this node type.

        Raises:
            WorkflowExecutionError: When the node's type has no handler
                registered in ``_NODE_HANDLER_REGISTRY``.
        """
        ctx = _NodeDispatchContext(
            nid=nid,
            qualified_id=qualified_id,
            node=node,
            adjacency=adjacency,
            reverse_adj=reverse_adj,
            outgoing=outgoing,
            frame=frame,
            frame_ctx=frame_ctx,
            execution_id=execution_id,
            project=project,
            activated_by=activated_by,
            node_map=node_map,
            frame_node_task_ids=frame_node_task_ids,
            qualifier_prefix=qualifier_prefix,
            state=state,
            skipped_nodes=skipped_nodes,
            pending_assignments=pending_assignments,
        )
        try:
            handler = _NODE_HANDLER_REGISTRY.get(node.type)
        except StrategyFactoryNotFoundError:
            msg = f"Unhandled node type {node.type.value!r} for node {nid!r}"
            logger.error(
                WORKFLOW_EXEC_NODE_COMPLETED,
                execution_id=execution_id,
                node_id=qualified_id,
                node_type=node.type.value,
                error=msg,
            )
            raise WorkflowExecutionError(msg) from None
        return await handler(self, ctx)

    async def _process_task_node_in_frame(  # noqa: PLR0913
        self,
        *,
        nid: str,
        qualified_id: str,
        node: WorkflowNode,
        reverse_adj: dict[str, list[str]],
        node_map: dict[str, WorkflowNode],
        frame_node_task_ids: dict[str, str | tuple[str, ...]],
        qualifier_prefix: str,  # noqa: ARG002
        state: WalkState,  # noqa: ARG002
        skipped_nodes: set[str],
        pending_assignments: dict[str, str],
        project: str,
        activated_by: str,
        execution_id: str,
    ) -> WorkflowNodeExecution:
        """Create a task for a TASK node and store it under the qualified key.

        Upstream dependency lookup is frame-local: it walks only this
        graph's reverse adjacency, which guarantees that a task inside
        a subworkflow does not silently depend on a parent task outside
        its declared inputs.

        Returns:
            The :class:`WorkflowNodeExecution` for this TASK node,
            with ``node_id`` rewritten to the frame-qualified key.
        """
        node_execution = await process_task_node(
            nid,
            node,
            reverse_adj=reverse_adj,
            node_map=node_map,
            node_task_ids=frame_node_task_ids,
            skipped_nodes=skipped_nodes,
            pending_assignments=pending_assignments,
            project=project,
            activated_by=activated_by,
            task_engine=self._task_engine,
            execution_id=execution_id,
        )
        # Rewrite node_id with the frame qualifier for persistence.
        return node_execution.model_copy(update={"node_id": qualified_id})

    async def _process_subworkflow_node(  # noqa: PLR0913
        self,
        *,
        nid: str,
        qualified_id: str,
        node: WorkflowNode,
        frame: ExecutionFrame,
        frame_ctx: dict[str, object],
        frame_node_task_ids: dict[str, str | tuple[str, ...]],
        state: WalkState,
        execution_id: str,
        project: str,
        activated_by: str,
    ) -> WorkflowNodeExecution:
        """Resolve a subworkflow node and walk the child graph in a new frame.

        Returns:
            The :class:`WorkflowNodeExecution` for the resolved
            subworkflow root, with the child frame walked and its
            outputs projected back into the parent scope.

        Raises:
            WorkflowExecutionError: If no ``SubworkflowRegistry`` is
                wired, or the node config is missing / has the wrong
                shape for ``subworkflow_id`` / ``version``.
            SubworkflowDepthExceededError: When the nested-frame depth
                exceeds the engine's configured maximum.
        """
        if self._subworkflow_registry is None:
            msg = (
                f"Workflow definition contains a SUBWORKFLOW node {nid!r} "
                "but no SubworkflowRegistry is configured on "
                "WorkflowExecutionService"
            )
            logger.warning(
                WORKFLOW_EXEC_SUBWORKFLOW_CONFIG_INVALID,
                node_id=nid,
                reason="registry_not_configured",
                error_type=WorkflowExecutionError.__name__,
            )
            raise WorkflowExecutionError(msg)

        config = dict(node.config)
        subworkflow_id = config.get("subworkflow_id")
        version = config.get("version")
        input_bindings = config.get("input_bindings")
        output_bindings = config.get("output_bindings")
        if not isinstance(subworkflow_id, str) or not subworkflow_id.strip():
            msg = f"SUBWORKFLOW node {nid!r} is missing subworkflow_id in config"
            logger.warning(
                WORKFLOW_EXEC_SUBWORKFLOW_CONFIG_INVALID,
                node_id=nid,
                reason="missing_subworkflow_id",
                error_type=WorkflowExecutionError.__name__,
            )
            raise WorkflowExecutionError(msg)
        if not isinstance(version, str) or not version.strip():
            msg = f"SUBWORKFLOW node {nid!r} is missing version pin in config"
            logger.warning(
                WORKFLOW_EXEC_SUBWORKFLOW_CONFIG_INVALID,
                node_id=nid,
                reason="missing_version",
                error_type=WorkflowExecutionError.__name__,
            )
            raise WorkflowExecutionError(msg)
        if not isinstance(input_bindings, dict):
            msg = (
                f"SUBWORKFLOW node {nid!r} input_bindings must be"
                f" a dict, got {type(input_bindings).__name__}"
            )
            logger.warning(
                WORKFLOW_EXEC_SUBWORKFLOW_CONFIG_INVALID,
                node_id=nid,
                reason="input_bindings_not_dict",
                error_type=WorkflowExecutionError.__name__,
            )
            raise WorkflowExecutionError(msg)
        if not isinstance(output_bindings, dict):
            msg = (
                f"SUBWORKFLOW node {nid!r} output_bindings must be"
                f" a dict, got {type(output_bindings).__name__}"
            )
            logger.warning(
                WORKFLOW_EXEC_SUBWORKFLOW_CONFIG_INVALID,
                node_id=nid,
                reason="output_bindings_not_dict",
                error_type=WorkflowExecutionError.__name__,
            )
            raise WorkflowExecutionError(msg)

        next_depth = frame.depth + 1
        if next_depth > self._max_subworkflow_depth:
            logger.error(
                WORKFLOW_EXEC_SUBWORKFLOW_DEPTH_EXCEEDED,
                execution_id=execution_id,
                node_id=qualified_id,
                depth=next_depth,
                max_depth=self._max_subworkflow_depth,
            )
            msg = (
                f"Subworkflow depth {next_depth} exceeds maximum "
                f"{self._max_subworkflow_depth}"
            )
            raise SubworkflowDepthExceededError(
                msg,
                depth=next_depth,
                max_depth=self._max_subworkflow_depth,
            )

        child_definition = await self._subworkflow_registry.get(
            subworkflow_id,
            version,
        )

        # Resolve input bindings against the parent frame's current
        # variable map (which may include values projected from earlier
        # subworkflow calls), producing the child frame's private
        # variable map.
        resolved_inputs = resolve_input_bindings(
            input_bindings,
            frame_ctx,
            child_definition.inputs,
        )
        child_frame = ExecutionFrame(
            workflow_id=str(child_definition.id),
            workflow_version=child_definition.version,
            variables=MappingProxyType(resolved_inputs),
            parent_frame=frame,
            depth=next_depth,
        )
        logger.info(
            WORKFLOW_EXEC_SUBWORKFLOW_FRAME_PUSHED,
            execution_id=execution_id,
            node_id=qualified_id,
            subworkflow_id=subworkflow_id,
            version=version,
            depth=next_depth,
        )

        # Recurse into the child graph.  Child node IDs are qualified
        # with the parent's subworkflow node ID to keep the flat
        # accumulator unique across frame boundaries.
        child_prefix = qualified_id
        child_final_vars = await self._walk_frame(
            definition=child_definition,
            frame=child_frame,
            qualifier_prefix=child_prefix,
            state=state,
            execution_id=execution_id,
            project=project,
            activated_by=activated_by,
        )

        # Project outputs back into the parent frame's mutable variable
        # map.  Downstream nodes in the parent graph will see the new
        # values via their own reads of frame_ctx.
        projected = project_output_bindings(
            output_bindings,
            child_final_vars,
            child_definition.outputs,
            parent_vars=frame_ctx,
        )
        frame_ctx.update(projected)

        # Record the child's terminal task IDs so that downstream
        # TASK nodes in the parent frame depend on the subworkflow's
        # final tasks (rather than having no dependency link at all).
        terminal_ids = collect_terminal_task_ids(
            child_definition,
            child_prefix,
            state,
        )
        if terminal_ids:
            frame_node_task_ids[nid] = terminal_ids

        logger.info(
            WORKFLOW_EXEC_SUBWORKFLOW_FRAME_POPPED,
            execution_id=execution_id,
            node_id=qualified_id,
            subworkflow_id=subworkflow_id,
            version=version,
            depth=next_depth,
        )
        logger.debug(
            WORKFLOW_EXEC_SUBWORKFLOW_NODE_COMPLETED,
            execution_id=execution_id,
            node_id=qualified_id,
        )
        return WorkflowNodeExecution(
            node_id=qualified_id,
            node_type=WorkflowNodeType.SUBWORKFLOW,
            status=WorkflowNodeExecutionStatus.SUBWORKFLOW_COMPLETED,
        )

    async def get_execution(
        self,
        execution_id: str,
    ) -> WorkflowExecution | None:
        """Retrieve a workflow execution by ID.

        Returns:
            The :class:`WorkflowExecution`, or ``None`` if no record
            matches.
        """
        return await lifecycle.get_execution(
            self._execution_repo,
            execution_id,
        )

    async def list_executions(
        self,
        definition_id: str | None = None,
        *,
        status: WorkflowExecutionStatus | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[WorkflowExecution, ...]:
        """List executions filtered by definition and/or status.

        Returns:
            The tuple of :class:`WorkflowExecution` rows matching the
            supplied filters (up to ``limit`` rows).
        """
        return await lifecycle.list_executions(
            self._execution_repo,
            definition_id,
            status=status,
            limit=limit,
        )

    async def cancel_execution(
        self,
        execution_id: str,
        *,
        cancelled_by: str,
    ) -> WorkflowExecution:
        """Cancel a workflow execution.

        Returns:
            The cancelled :class:`WorkflowExecution`.
        """
        return await lifecycle.cancel_execution(
            self._execution_repo,
            execution_id,
            cancelled_by=cancelled_by,
        )

    async def complete_execution(
        self,
        execution_id: str,
    ) -> WorkflowExecution:
        """Transition a running execution to COMPLETED.

        Returns:
            The completed :class:`WorkflowExecution`.
        """
        return await lifecycle.complete_execution(
            self._execution_repo,
            execution_id,
        )

    async def fail_execution(
        self,
        execution_id: str,
        *,
        error: str,
    ) -> WorkflowExecution:
        """Transition a running execution to FAILED.

        Returns:
            The failed :class:`WorkflowExecution`.
        """
        return await lifecycle.fail_execution(
            self._execution_repo,
            execution_id,
            error=error,
        )

    async def handle_task_state_changed(
        self,
        event: TaskStateChanged,
    ) -> None:
        """React to a task state change from the TaskEngine."""
        await lifecycle.handle_task_state_changed(
            self._execution_repo,
            event,
        )
