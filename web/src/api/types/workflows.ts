/** Workflow definition, execution, versioning and blueprint types. */

export type {
  ActivateWorkflowRequest,
  BlueprintInfoResponse as BlueprintInfo,
  CreateFromBlueprintRequest,
  CreateSubworkflowRequest,
  CreateWorkflowDefinitionRequest,
  MetadataChange,
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

import type {
  EdgeChange as WireEdgeChange,
  NodeChange as WireNodeChange,
} from './dtos.gen'
import {
  WORKFLOW_EDGE_TYPE_VALUES,
  WORKFLOW_NODE_TYPE_VALUES,
} from './enum-values.gen'
import type {
  WorkflowEdgeType,
  WorkflowNodeType,
} from './enum-values.gen'
import type { WorkflowNodeType as WireWorkflowNodeType } from './enum-values.gen'

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

// NodeChange / EdgeChange overlay the wire shape's optional+nullable
// ``old_value`` / ``new_value`` fields as required+nullable: the diff
// renderer always populates them (the optionality on the wire reflects
// Pydantic's permissive serialiser, not absence semantics). MetadataChange
// is byte-identical to the wire shape and is re-exported directly above.
export type NodeChange = Omit<WireNodeChange, 'old_value' | 'new_value'> & {
  readonly old_value: Record<string, unknown> | null
  readonly new_value: Record<string, unknown> | null
}

export type EdgeChange = Omit<WireEdgeChange, 'old_value' | 'new_value'> & {
  readonly old_value: Record<string, unknown> | null
  readonly new_value: Record<string, unknown> | null
}
