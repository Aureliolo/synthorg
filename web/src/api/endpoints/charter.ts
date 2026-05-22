import { apiClient, unwrap } from '../client'
import type {
  CharterApprovalResult,
  CharterEditRequest,
  InterviewTurnRequest,
  InterviewTurnResult,
  ProjectCharter,
} from '../types'
import type { ApiResponse } from '../types/http'

export interface CharterFilters {
  status?: string
  project_id?: string
  limit?: number
  offset?: number
}

const BASE = '/meta/charters'

export async function listCharters(
  filters?: CharterFilters,
): Promise<ProjectCharter[]> {
  const response = await apiClient.get<ApiResponse<ProjectCharter[]>>(BASE, {
    params: filters,
  })
  return unwrap(response)
}

export async function getCharter(id: string): Promise<ProjectCharter> {
  const response = await apiClient.get<ApiResponse<ProjectCharter>>(
    `${BASE}/${encodeURIComponent(id)}`,
  )
  return unwrap(response)
}

export async function runInterviewTurn(
  data: InterviewTurnRequest,
): Promise<InterviewTurnResult> {
  const response = await apiClient.post<ApiResponse<InterviewTurnResult>>(
    `${BASE}/interview`,
    data,
  )
  return unwrap(response)
}

export async function editCharter(
  id: string,
  data: CharterEditRequest,
): Promise<ProjectCharter> {
  const response = await apiClient.patch<ApiResponse<ProjectCharter>>(
    `${BASE}/${encodeURIComponent(id)}`,
    data,
  )
  return unwrap(response)
}

export async function approveCharter(
  id: string,
): Promise<CharterApprovalResult> {
  const response = await apiClient.post<ApiResponse<CharterApprovalResult>>(
    `${BASE}/${encodeURIComponent(id)}/approve`,
    {},
  )
  return unwrap(response)
}

export async function cancelCharter(id: string): Promise<ProjectCharter> {
  const response = await apiClient.post<ApiResponse<ProjectCharter>>(
    `${BASE}/${encodeURIComponent(id)}/cancel`,
    {},
  )
  return unwrap(response)
}
