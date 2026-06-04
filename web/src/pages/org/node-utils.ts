import type { Node } from '@xyflow/react'
import type { AgentNodeData, DepartmentGroupData, OwnerNodeData } from './build-org-tree'

/** Non-null, non-array, object-shaped value (shared pre-check for shape guards). */
function isObjectRecord(data: unknown): data is Record<string, unknown> {
  return typeof data === 'object' && data !== null && !Array.isArray(data)
}

/**
 * Build a type predicate that asserts every listed field is a non-empty
 * `string` on the input object. Used by the node-data shape guards so each
 * one validates the full string-typed surface of its interface, not just
 * one field.
 */
function makeStringFieldGuard<T>(
  requiredStringFields: readonly (keyof T & string)[],
): (data: unknown) => data is T {
  return (data: unknown): data is T => {
    if (!isObjectRecord(data)) return false
    for (const key of requiredStringFields) {
      const value = (data)[key]
      // Reject whitespace-only strings as well as empty ones -- a label
      // like '   ' would otherwise pass this guard and surface as a
      // blank name / role / id in the UI instead of falling back to
      // node.id via the outer code path.
      if (typeof value !== 'string' || value.trim().length === 0) return false
    }
    return true
  }
}

const isAgentNodeData = makeStringFieldGuard<AgentNodeData>([
  'agentId',
  'name',
  'role',
  'department',
  'level',
  'runtimeStatus',
])

const isDepartmentGroupData = makeStringFieldGuard<DepartmentGroupData>([
  'departmentName',
  'displayName',
])

const isOwnerNodeData = makeStringFieldGuard<OwnerNodeData>([
  'ownerId',
  'displayName',
  'role',
])

/**
 * Resolve a display label for an org-chart node.
 *
 * Falls back to `node.id` when the node has no recognised type, when the
 * data shape fails the guard for the matched type, or when the type is
 * absent. The shape guards reject blank/whitespace-only fields so a stale
 * record never surfaces an empty label in the UI.
 */
export function getNodeLabel(node: Node): string {
  switch (node.type) {
    case 'agent':
    case 'ceo':
      return isAgentNodeData(node.data) ? node.data.name : node.id
    case 'department':
      return isDepartmentGroupData(node.data) ? node.data.displayName : node.id
    case 'owner':
      return isOwnerNodeData(node.data) ? node.data.displayName : node.id
    default:
      return node.id
  }
}
