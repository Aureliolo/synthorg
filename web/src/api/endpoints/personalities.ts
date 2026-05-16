/**
 * Personality preset admin endpoints.
 *
 * Distinct from ``setup.ts`` listPersonalityPresets which serves the
 * setup wizard's read-only preset picker. The admin surface exposes
 * the full CRUD shape over /personalities/presets so operators can
 * create / update / delete custom personality presets.
 */
import { apiClient, paginateAll, unwrap, unwrapPaginated } from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'
import type {
  CreatePresetRequest,
  PresetDetailResponse,
  PresetSummaryResponse,
  UpdatePresetRequest,
} from '../types/dtos.gen'

export async function listAdminPresets(): Promise<readonly PresetSummaryResponse[]> {
  return paginateAll<PresetSummaryResponse>(async (cursor) => {
    const qs = cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''
    const response = await apiClient.get<PaginatedResponse<PresetSummaryResponse>>(
      `/personalities/presets${qs}`,
    )
    return unwrapPaginated(response)
  })
}

export async function getAdminPreset(name: string): Promise<PresetDetailResponse> {
  const response = await apiClient.get<ApiResponse<PresetDetailResponse>>(
    `/personalities/presets/${encodeURIComponent(name)}`,
  )
  return unwrap(response)
}

export async function createAdminPreset(
  data: CreatePresetRequest,
): Promise<PresetDetailResponse> {
  const response = await apiClient.post<ApiResponse<PresetDetailResponse>>(
    '/personalities/presets',
    data,
  )
  return unwrap(response)
}

export async function updateAdminPreset(
  name: string,
  data: UpdatePresetRequest,
): Promise<PresetDetailResponse> {
  const response = await apiClient.put<ApiResponse<PresetDetailResponse>>(
    `/personalities/presets/${encodeURIComponent(name)}`,
    data,
  )
  return unwrap(response)
}

export async function deleteAdminPreset(name: string): Promise<void> {
  await apiClient.delete<ApiResponse<null>>(
    `/personalities/presets/${encodeURIComponent(name)}`,
  )
}
