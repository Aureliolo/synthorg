/** Workflow definition, execution, versioning and blueprint types. */

export type {
  ActivateWorkflowRequest,
  BlueprintInfoResponse as BlueprintInfo,
  CreateFromBlueprintRequest,
  CreateSubworkflowRequest,
  CreateWorkflowDefinitionRequest,
  ParentReference,
  RollbackWorkflowRequest,
  SubworkflowSummary,
  UpdateWorkflowDefinitionRequest,
  WorkflowDefinition,
  WorkflowDiff,
  WorkflowExecution,
  WorkflowIODeclaration,
  WorkflowIODeclarationRequest,
  WorkflowValidationError,
  WorkflowValidationResult,
} from './dtos.gen'

export type {
  WorkflowEdgeType,
  WorkflowExecutionStatus,
  WorkflowNodeExecutionStatus,
  WorkflowNodeType,
  WorkflowValueType,
} from './enum-values.gen'

export {
  WORKFLOW_EDGE_TYPE_VALUES,
  WORKFLOW_EXECUTION_STATUS_VALUES,
  WORKFLOW_NODE_EXECUTION_STATUS_VALUES,
  WORKFLOW_NODE_TYPE_VALUES,
  WORKFLOW_VALUE_TYPE_VALUES,
} from './enum-values.gen'

import {
  WORKFLOW_EDGE_TYPE_VALUES,
  WORKFLOW_NODE_TYPE_VALUES,
} from './enum-values.gen'
import type { WorkflowEdgeType, WorkflowNodeType } from './enum-values.gen'
import type { WorkflowNodeType as WireWorkflowNodeType } from './enum-values.gen'

/** Legacy aliases for the older import paths used by the workflow canvas
 *  components and tests (the dashboard had its own VALUES tuples; these
 *  re-export the generated names under the older identifiers). */
export const WORKFLOW_NODE_TYPES = WORKFLOW_NODE_TYPE_VALUES
export const WORKFLOW_EDGE_TYPES = WORKFLOW_EDGE_TYPE_VALUES

export function isWorkflowNodeType(value: unknown): value is WorkflowNodeType {
  return (
    typeof value === 'string'
    && (WORKFLOW_NODE_TYPE_VALUES as readonly string[]).includes(value)
  )
}

export function isWorkflowEdgeType(value: unknown): value is WorkflowEdgeType {
  return (
    typeof value === 'string'
    && (WORKFLOW_EDGE_TYPE_VALUES as readonly string[]).includes(value)
  )
}

/** Frontend-only node / edge / execution shapes used by the React Flow
 *  canvas. The wire emits these as embedded ``dict`` payloads on
 *  WorkflowDefinition.nodes / edges so they do not have named
 *  ``components.schemas`` entries. */
export interface WorkflowNodeData {
  readonly id: string
  readonly type: WireWorkflowNodeType
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

export interface WorkflowNodeExecution {
  readonly node_id: string
  readonly node_type: WireWorkflowNodeType
  readonly status: import('./enum-values.gen').WorkflowNodeExecutionStatus
  readonly task_id: string | null
  readonly skipped_reason: string | null
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
