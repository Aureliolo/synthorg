"""Workflow subsystem enumerations."""

from enum import StrEnum


class WorkflowType(StrEnum):
    """Workflow type for organizing task execution.

    Matches the four workflow types defined in the Engine design page
    (docs/design/engine.md, Workflow Types section).
    """

    SEQUENTIAL_PIPELINE = "sequential_pipeline"
    PARALLEL_EXECUTION = "parallel_execution"
    KANBAN = "kanban"
    AGILE_KANBAN = "agile_kanban"


class WorkflowNodeType(StrEnum):
    """Node type in a visual workflow definition.

    Each node represents a step or control-flow element in the
    visual workflow editor.
    """

    START = "start"
    END = "end"
    TASK = "task"
    AGENT_ASSIGNMENT = "agent_assignment"
    CONDITIONAL = "conditional"
    PARALLEL_SPLIT = "parallel_split"
    PARALLEL_JOIN = "parallel_join"
    SUBWORKFLOW = "subworkflow"
    VERIFICATION = "verification"


class WorkflowValueType(StrEnum):
    """Typed value kinds for workflow I/O declarations.

    Used by :class:`WorkflowIODeclaration` to enforce typed contracts
    on subworkflow inputs and outputs at save time and at runtime.
    """

    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    JSON = "json"
    TASK_REF = "task_ref"
    AGENT_REF = "agent_ref"


class WorkflowEdgeType(StrEnum):
    """Edge type connecting nodes in a visual workflow definition.

    Encodes the relationship semantics between workflow nodes.
    """

    SEQUENTIAL = "sequential"
    CONDITIONAL_TRUE = "conditional_true"
    CONDITIONAL_FALSE = "conditional_false"
    PARALLEL_BRANCH = "parallel_branch"
    VERIFICATION_PASS = "verification_pass"  # noqa: S105
    VERIFICATION_FAIL = "verification_fail"
    VERIFICATION_REFER = "verification_refer"


class WorkflowExecutionStatus(StrEnum):
    """Lifecycle status of a workflow execution instance.

    Tracks the overall progress of an activated workflow definition
    from creation through completion or cancellation.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowNodeExecutionStatus(StrEnum):
    """Per-node execution status within a workflow execution.

    Tracks whether each node in the workflow graph has been
    processed, skipped (conditional branch not taken), or
    resulted in a concrete task.
    """

    PENDING = "pending"
    SKIPPED = "skipped"
    TASK_CREATED = "task_created"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    COMPLETED = "completed"
    SUBWORKFLOW_COMPLETED = "subworkflow_completed"
