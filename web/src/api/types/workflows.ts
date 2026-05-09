/** Workflow definition, execution, versioning and blueprint types. */

export type WorkflowNodeType =
  | 'start'
  | 'end'
  | 'task'
  | 'agent_assignment'
  | 'conditional'
  | 'parallel_split'
  | 'parallel_join'
  | 'subworkflow'

export type WorkflowEdgeType =
  | 'sequential'
  | 'conditional_true'
  | 'conditional_false'
  | 'parallel_branch'

export interface WorkflowNodeData {
  readonly id: string
  readonly type: WorkflowNodeType
  readonly label: string
  readonly position_x: number
  readonly position_y: number
  readonly config: Record<string, unknown>
}

export interface WorkflowEdgeData {
  readonly id: string
  readonly source_node_id: string
  readonly target_node_id: string
  readonly type: WorkflowEdgeType
  readonly label: string | null
}

export type WorkflowValueType =
  | 'string'
  | 'integer'
  | 'float'
  | 'boolean'
  | 'datetime'
  | 'json'
  | 'task_ref'
  | 'agent_ref'

export interface WorkflowIODeclaration {
  readonly name: string
  readonly type: WorkflowValueType
  readonly required: boolean
  readonly default: unknown
  readonly description: string
}

export interface WorkflowDefinition {
  readonly id: string
  readonly name: string
  readonly description: string
  readonly workflow_type: string
  readonly version: string
  readonly inputs: readonly WorkflowIODeclaration[]
  readonly outputs: readonly WorkflowIODeclaration[]
  readonly is_subworkflow: boolean
  readonly nodes: readonly WorkflowNodeData[]
  readonly edges: readonly WorkflowEdgeData[]
  readonly created_by: string
  readonly created_at: string
  readonly updated_at: string
  readonly revision: number
}

export interface CreateWorkflowDefinitionRequest {
  readonly name: string
  readonly description?: string
  readonly workflow_type: string
  readonly version?: string
  readonly inputs?: readonly WorkflowIODeclaration[]
  readonly outputs?: readonly WorkflowIODeclaration[]
  readonly is_subworkflow?: boolean
  readonly nodes: readonly WorkflowNodeData[]
  readonly edges: readonly WorkflowEdgeData[]
}

export interface UpdateWorkflowDefinitionRequest {
  readonly name?: string
  readonly description?: string
  readonly workflow_type?: string
  readonly version?: string
  readonly inputs?: readonly Record<string, unknown>[]
  readonly outputs?: readonly Record<string, unknown>[]
  readonly is_subworkflow?: boolean
  readonly nodes?: readonly Record<string, unknown>[]
  readonly edges?: readonly Record<string, unknown>[]
  readonly expected_revision?: number
}

export interface SubworkflowSummary {
  readonly subworkflow_id: string
  readonly latest_version: string
  readonly name: string
  readonly description: string
  readonly input_count: number
  readonly output_count: number
  readonly version_count: number
}

export interface ParentReference {
  readonly parent_id: string
  readonly parent_name: string
  readonly pinned_version: string
  readonly node_id: string
  readonly parent_type: 'workflow_definition' | 'subworkflow'
}

export interface CreateSubworkflowRequest {
  readonly subworkflow_id?: string
  readonly version?: string
  readonly name: string
  readonly description?: string
  readonly workflow_type: string
  readonly inputs?: readonly Record<string, unknown>[]
  readonly outputs?: readonly Record<string, unknown>[]
  readonly nodes: readonly Record<string, unknown>[]
  readonly edges: readonly Record<string, unknown>[]
}

export interface WorkflowValidationError {
  readonly code: string
  readonly message: string
  readonly node_id: string | null
  readonly edge_id: string | null
}

export interface WorkflowValidationResult {
  readonly valid: boolean
  readonly errors: readonly WorkflowValidationError[]
}

export type WorkflowExecutionStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type WorkflowNodeExecutionStatus =
  | 'pending'
  | 'skipped'
  | 'task_created'
  | 'task_completed'
  | 'task_failed'
  | 'completed'
  | 'subworkflow_completed'

export interface WorkflowNodeExecution {
  readonly node_id: string
  readonly node_type: WorkflowNodeType
  readonly status: WorkflowNodeExecutionStatus
  readonly task_id: string | null
  readonly skipped_reason: string | null
}

export interface WorkflowExecution {
  readonly id: string
  readonly definition_id: string
  readonly definition_revision: number
  readonly status: WorkflowExecutionStatus
  readonly node_executions: readonly WorkflowNodeExecution[]
  readonly activated_by: string
  readonly project: string
  readonly created_at: string
  readonly updated_at: string
  readonly completed_at: string | null
  readonly error: string | null
  readonly version: number
}

export interface ActivateWorkflowRequest {
  readonly project: string
  readonly context?: Record<string, string | number | boolean | null>
}

export interface BlueprintInfo {
  readonly name: string
  readonly display_name: string
  readonly description: string
  readonly source: 'builtin' | 'user'
  readonly tags: readonly string[]
  readonly workflow_type: string
  readonly node_count: number
  readonly edge_count: number
}

export interface CreateFromBlueprintRequest {
  readonly blueprint_name: string
  readonly name?: string
  readonly description?: string
}

/** Generic version snapshot envelope matching backend VersionSnapshot[T]. */
export interface VersionSummary<TSnapshot> {
  readonly entity_id: string
  readonly version: number
  readonly content_hash: string
  readonly snapshot: TSnapshot
  readonly saved_by: string
  readonly saved_at: string
}

export interface WorkflowDefinitionSnapshot {
  readonly id: string
  readonly name: string
  readonly description: string
  readonly workflow_type: string
  readonly nodes: readonly WorkflowNodeData[]
  readonly edges: readonly WorkflowEdgeData[]
  readonly created_by: string
}

export type WorkflowDefinitionVersionSummary = VersionSummary<WorkflowDefinitionSnapshot>

export interface NodeChange {
  readonly node_id: string
  readonly change_type:
    | 'added'
    | 'removed'
    | 'moved'
    | 'config_changed'
    | 'label_changed'
    | 'type_changed'
  readonly old_value: Record<string, unknown> | null
  readonly new_value: Record<string, unknown> | null
}

export interface EdgeChange {
  readonly edge_id: string
  readonly change_type:
    | 'added'
    | 'removed'
    | 'reconnected'
    | 'type_changed'
    | 'label_changed'
  readonly old_value: Record<string, unknown> | null
  readonly new_value: Record<string, unknown> | null
}

export interface MetadataChange {
  readonly field: string
  readonly old_value: string
  readonly new_value: string
}

export interface WorkflowDiff {
  readonly definition_id: string
  readonly from_version: number
  readonly to_version: number
  readonly node_changes: readonly NodeChange[]
  readonly edge_changes: readonly EdgeChange[]
  readonly metadata_changes: readonly MetadataChange[]
  readonly summary: string
}

export interface RollbackWorkflowRequest {
  readonly target_version: number
  readonly expected_revision: number
}
