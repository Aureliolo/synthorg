import { apiClient, unwrap, unwrapPaginated } from '../client'
import type {
  ApiResponse,
  ApprovalFilters,
  ApprovalItem,
  ApproveRequest,
  CreateApprovalRequest,
  PaginatedResponse,
  RejectRequest,
} from '../types'

export async function listApprovals(filters?: ApprovalFilters) {
  const response = await apiClient.get<PaginatedResponse<ApprovalItem>>('/approvals', { params: filters })
  return unwrapPaginated<ApprovalItem>(response)
}

export async function getApproval(id: string): Promise<ApprovalItem> {
  const response = await apiClient.get<ApiResponse<ApprovalItem>>(`/approvals/${encodeURIComponent(id)}`)
  return unwrap(response)
}

export async function createApproval(data: CreateApprovalRequest): Promise<ApprovalItem> {
  const response = await apiClient.post<ApiResponse<ApprovalItem>>('/approvals', data)
  return unwrap(response)
}

export async function approveApproval(id: string, data?: ApproveRequest): Promise<ApprovalItem> {
  const response = await apiClient.post<ApiResponse<ApprovalItem>>(`/approvals/${encodeURIComponent(id)}/approve`, data ?? {})
  return unwrap(response)
}

export async function rejectApproval(id: string, data: RejectRequest): Promise<ApprovalItem> {
  const response = await apiClient.post<ApiResponse<ApprovalItem>>(`/approvals/${encodeURIComponent(id)}/reject`, data)
  return unwrap(response)
}
