import { apiClient, unwrap, unwrapVoid } from '../client'
import type {
  ActiveCeremonyStrategy,
  CeremonyPolicyConfig,
  ResolvedCeremonyPolicyResponse,
} from '../types/ceremony-policy'
import type { ApiResponse } from '../types/http'

export async function getCeremonyPolicy(): Promise<CeremonyPolicyConfig> {
  const response = await apiClient.get<ApiResponse<CeremonyPolicyConfig>>(
    '/ceremony-policy',
  )
  return unwrap(response)
}

export async function getResolvedPolicy(
  department?: string,
): Promise<ResolvedCeremonyPolicyResponse> {
  const response = await apiClient.get<ApiResponse<ResolvedCeremonyPolicyResponse>>(
    '/ceremony-policy/resolved',
    { params: department ? { department } : undefined },
  )
  return unwrap(response)
}

export async function getActiveStrategy(): Promise<ActiveCeremonyStrategy> {
  const response = await apiClient.get<ApiResponse<ActiveCeremonyStrategy>>(
    '/ceremony-policy/active',
  )
  return unwrap(response)
}

export async function getDepartmentCeremonyPolicy(
  name: string,
): Promise<CeremonyPolicyConfig | null> {
  const response = await apiClient.get<ApiResponse<CeremonyPolicyConfig | null>>(
    `/departments/${encodeURIComponent(name)}/ceremony-policy`,
  )
  return unwrap(response)
}

export async function updateDepartmentCeremonyPolicy(
  name: string,
  data: CeremonyPolicyConfig,
): Promise<CeremonyPolicyConfig> {
  const response = await apiClient.put<ApiResponse<CeremonyPolicyConfig>>(
    `/departments/${encodeURIComponent(name)}/ceremony-policy`,
    data,
  )
  return unwrap(response)
}

export async function clearDepartmentCeremonyPolicy(
  name: string,
): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(
    `/departments/${encodeURIComponent(name)}/ceremony-policy`,
  )
  unwrapVoid(response)
}
