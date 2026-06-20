import { apiClient, unwrap, unwrapPaginated, type PaginatedResult } from '../client'
import { idempotencyKeyHeader } from '../idempotency'
import type {
  ApprovalFilters,
  ApprovalResponse,
  ApproveRequest,
  CreateApprovalRequest,
  RejectRequest,
} from '../types/approvals'
import type { ApiResponse, PaginatedResponse } from '../types/http'

export async function listApprovals(filters?: ApprovalFilters): Promise<PaginatedResult<ApprovalResponse>> {
  const response = await apiClient.get<PaginatedResponse<ApprovalResponse>>('/approvals', { params: filters })
  return unwrapPaginated<ApprovalResponse>(response)
}

export async function getApproval(id: string): Promise<ApprovalResponse> {
  const response = await apiClient.get<ApiResponse<ApprovalResponse>>(`/approvals/${encodeURIComponent(id)}`)
  return unwrap(response)
}

export async function createApproval(data: CreateApprovalRequest): Promise<ApprovalResponse> {
  const response = await apiClient.post<ApiResponse<ApprovalResponse>>('/approvals', data)
  return unwrap(response)
}

export async function approveApproval(
  id: string,
  data?: ApproveRequest,
  idempotencyKey?: string,
): Promise<ApprovalResponse> {
  // The backend requires the Idempotency-Key header on the decision so a
  // 5xx-driven retry cannot re-fire the notify / resume-signal side effects.
  const response = await apiClient.post<ApiResponse<ApprovalResponse>>(
    `/approvals/${encodeURIComponent(id)}/approve`,
    data ?? {},
    { headers: idempotencyKeyHeader(idempotencyKey) },
  )
  return unwrap(response)
}

export async function rejectApproval(
  id: string,
  data: RejectRequest,
  idempotencyKey?: string,
): Promise<ApprovalResponse> {
  const response = await apiClient.post<ApiResponse<ApprovalResponse>>(
    `/approvals/${encodeURIComponent(id)}/reject`,
    data,
    { headers: idempotencyKeyHeader(idempotencyKey) },
  )
  return unwrap(response)
}
