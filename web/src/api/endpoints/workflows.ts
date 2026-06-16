import { apiClient, unwrap, unwrapPaginated, unwrapVoid, type PaginatedResult } from '../client'
import type { VersionDiffResponse } from './version-history'
import type { ApiResponse, PaginatedResponse } from '../types/http'
import type {
  BlueprintInfo,
  CreateFromBlueprintRequest,
  CreateWorkflowDefinitionRequest,
  RollbackWorkflowRequest,
  UpdateWorkflowDefinitionRequest,
  WorkflowDefinition,
  WorkflowDefinitionVersionSummary,
  WorkflowDiff,
  WorkflowValidationResult,
} from '../types/workflows'

export async function listWorkflows(filters?: {
  workflow_type?: string
  /** Opaque pagination cursor from the previous response's `pagination.next_cursor`. */
  cursor?: string | null
  limit?: number
}): Promise<PaginatedResult<WorkflowDefinition>> {
  const response = await apiClient.get<PaginatedResponse<WorkflowDefinition>>(
    '/workflows',
    { params: filters },
  )
  return unwrapPaginated<WorkflowDefinition>(response)
}

export async function getWorkflow(id: string): Promise<WorkflowDefinition> {
  const response = await apiClient.get<ApiResponse<WorkflowDefinition>>(
    `/workflows/${encodeURIComponent(id)}`,
  )
  return unwrap(response)
}

export async function createWorkflow(
  data: CreateWorkflowDefinitionRequest,
): Promise<WorkflowDefinition> {
  const response = await apiClient.post<ApiResponse<WorkflowDefinition>>(
    '/workflows',
    data,
  )
  return unwrap(response)
}

export async function updateWorkflow(
  id: string,
  data: UpdateWorkflowDefinitionRequest,
): Promise<WorkflowDefinition> {
  const response = await apiClient.patch<ApiResponse<WorkflowDefinition>>(
    `/workflows/${encodeURIComponent(id)}`,
    data,
  )
  return unwrap(response)
}

export async function deleteWorkflow(id: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(`/workflows/${encodeURIComponent(id)}`)
  unwrapVoid(response)
}

export async function validateWorkflow(id: string): Promise<WorkflowValidationResult> {
  const response = await apiClient.post<ApiResponse<WorkflowValidationResult>>(
    `/workflows/${encodeURIComponent(id)}/validate`,
  )
  return unwrap(response)
}

export async function validateWorkflowDraft(
  data: CreateWorkflowDefinitionRequest,
): Promise<WorkflowValidationResult> {
  const response = await apiClient.post<ApiResponse<WorkflowValidationResult>>(
    '/workflows/validate-draft',
    data,
  )
  return unwrap(response)
}

export async function listBlueprints(): Promise<readonly BlueprintInfo[]> {
  const response = await apiClient.get<ApiResponse<readonly BlueprintInfo[]>>(
    '/workflows/blueprints',
  )
  return unwrap(response)
}

export async function createFromBlueprint(
  data: CreateFromBlueprintRequest,
): Promise<WorkflowDefinition> {
  const response = await apiClient.post<ApiResponse<WorkflowDefinition>>(
    '/workflows/from-blueprint',
    data,
  )
  return unwrap(response)
}

export async function exportWorkflowYaml(id: string): Promise<string> {
  const response = await apiClient.post<string>(
    `/workflows/${encodeURIComponent(id)}/export`,
    undefined,
    { responseType: 'text' },
  )
  return response.data
}

// ── Version history ────────────────────────────────────────

export async function listWorkflowVersions(
  id: string,
  params?: { cursor?: string | null; limit?: number },
): Promise<PaginatedResult<WorkflowDefinitionVersionSummary>> {
  const response = await apiClient.get<PaginatedResponse<WorkflowDefinitionVersionSummary>>(
    `/workflows/${encodeURIComponent(id)}/versions`,
    { params },
  )
  return unwrapPaginated<WorkflowDefinitionVersionSummary>(response)
}

export async function getWorkflowVersion(
  id: string,
  version: number,
): Promise<WorkflowDefinitionVersionSummary> {
  const response = await apiClient.get<ApiResponse<WorkflowDefinitionVersionSummary>>(
    `/workflows/${encodeURIComponent(id)}/versions/${version}`,
  )
  return unwrap(response)
}

export async function getWorkflowDiff(
  id: string,
  fromVersion: number,
  toVersion: number,
): Promise<WorkflowDiff> {
  const response = await apiClient.get<ApiResponse<WorkflowDiff>>(
    `/workflows/${encodeURIComponent(id)}/diff`,
    { params: { from_version: fromVersion, to_version: toVersion } },
  )
  return unwrap(response)
}

/**
 * Diff two workflow versions, normalised for the shared diff drawer.
 *
 * Calls ``GET /workflows/{id}/diff`` (returns ``WorkflowDiff`` with
 * separate ``node_changes`` / ``edge_changes`` / ``metadata_changes``
 * lists) and flattens all three -- prefixing each path with its change
 * domain so node, edge, and metadata entries stay distinguishable -- into
 * the cross-domain ``VersionDiffResponse`` shape the drawer renders.
 */
export async function diffWorkflowVersions(
  id: string,
  fromVersion: number,
  toVersion: number,
): Promise<VersionDiffResponse> {
  const diff = await getWorkflowDiff(id, fromVersion, toVersion)
  return {
    from_version: diff.from_version,
    to_version: diff.to_version,
    entries: [
      ...diff.node_changes.map((change) => ({
        path: `node:${change.node_id}`,
        before: change.old_value,
        after: change.new_value,
      })),
      ...diff.edge_changes.map((change) => ({
        path: `edge:${change.edge_id}`,
        before: change.old_value,
        after: change.new_value,
      })),
      ...diff.metadata_changes.map((change) => ({
        path: `metadata:${change.field}`,
        before: change.old_value,
        after: change.new_value,
      })),
    ],
  }
}

export async function rollbackWorkflow(
  id: string,
  data: RollbackWorkflowRequest,
): Promise<WorkflowDefinition> {
  const response = await apiClient.post<ApiResponse<WorkflowDefinition>>(
    `/workflows/${encodeURIComponent(id)}/rollback`,
    data,
  )
  return unwrap(response)
}
