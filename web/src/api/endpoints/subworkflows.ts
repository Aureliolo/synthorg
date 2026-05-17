import { apiClient, unwrap, unwrapPaginated, unwrapVoid, type PaginatedResult } from '../client'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '../types/http'
import type {
  CreateSubworkflowRequest,
  ParentReference,
  SubworkflowSummary,
  WorkflowDefinition,
} from '../types/workflows'

export async function listSubworkflows(
  params?: PaginationParams,
): Promise<PaginatedResult<SubworkflowSummary>> {
  const response = await apiClient.get<PaginatedResponse<SubworkflowSummary>>(
    '/subworkflows',
    { params },
  )
  return unwrapPaginated<SubworkflowSummary>(response)
}

export async function searchSubworkflows(
  query: string,
  params?: PaginationParams,
): Promise<PaginatedResult<SubworkflowSummary>> {
  const response = await apiClient.get<PaginatedResponse<SubworkflowSummary>>(
    '/subworkflows/search',
    { params: { q: query, ...params } },
  )
  return unwrapPaginated<SubworkflowSummary>(response)
}

export async function listVersions(
  subworkflowId: string,
  params?: PaginationParams,
): Promise<PaginatedResult<string>> {
  const response = await apiClient.get<PaginatedResponse<string>>(
    `/subworkflows/${encodeURIComponent(subworkflowId)}/versions`,
    { params },
  )
  return unwrapPaginated<string>(response)
}

export async function getVersion(
  subworkflowId: string,
  version: string,
): Promise<WorkflowDefinition> {
  const response = await apiClient.get<ApiResponse<WorkflowDefinition>>(
    `/subworkflows/${encodeURIComponent(subworkflowId)}/versions/${encodeURIComponent(version)}`,
  )
  return unwrap(response)
}

export async function listParents(
  subworkflowId: string,
  version: string,
  params?: PaginationParams,
): Promise<PaginatedResult<ParentReference>> {
  const response = await apiClient.get<PaginatedResponse<ParentReference>>(
    `/subworkflows/${encodeURIComponent(subworkflowId)}/versions/${encodeURIComponent(version)}/parents`,
    { params },
  )
  return unwrapPaginated<ParentReference>(response)
}

export async function createSubworkflow(
  data: CreateSubworkflowRequest,
): Promise<WorkflowDefinition> {
  const response = await apiClient.post<ApiResponse<WorkflowDefinition>>(
    '/subworkflows',
    data,
  )
  return unwrap(response)
}

export async function deleteSubworkflow(
  subworkflowId: string,
  version: string,
): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(
    `/subworkflows/${encodeURIComponent(subworkflowId)}/versions/${encodeURIComponent(version)}`,
  )
  unwrapVoid(response)
}
