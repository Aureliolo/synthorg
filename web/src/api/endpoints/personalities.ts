/**
 * Personality preset admin endpoints.
 *
 * Distinct from ``setup.ts`` listPersonalityPresets which serves the
 * setup wizard's read-only preset picker. The admin surface exposes
 * the full CRUD shape over /personalities/presets so operators can
 * create / update / delete custom personality presets.
 */
import { apiClient, unwrap, unwrapPaginated, type PaginatedResult } from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'
import type {
  CreatePresetRequest,
  PresetDetailResponse,
  PresetSummaryResponse,
  UpdatePresetRequest,
} from '@/api/types'

export interface ListAdminPresetsParams {
  readonly cursor?: string | null
  readonly limit?: number | null
}

/**
 * Single-page admin preset listing.
 *
 * Returns the raw cursor metadata (``nextCursor`` / ``hasMore``) so
 * callers can paginate per the project's MANDATORY cursor-pagination
 * convention. The previous shape eagerly materialised every page via
 * ``paginateAll``, which lost the cursor envelope and forced
 * client-side pagination on top of an already-paginated server.
 */
export async function listAdminPresets(
  params: ListAdminPresetsParams = {},
): Promise<PaginatedResult<PresetSummaryResponse>> {
  const query = new URLSearchParams()
  if (params.cursor) query.set('cursor', params.cursor)
  if (params.limit != null) query.set('limit', String(params.limit))
  const qs = query.toString()
  const url = qs ? `/personalities/presets?${qs}` : '/personalities/presets'
  const response = await apiClient.get<PaginatedResponse<PresetSummaryResponse>>(url)
  return unwrapPaginated(response)
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

/**
 * Fetch the personality-preset JSON schema (Big-Five axes,
 * behavioural enums, constraints) used by the create / edit forms to
 * render and validate inputs. The backend returns an opaque
 * ``dict[str, Any]`` envelope.
 */
export async function getPersonalitiesSchema(): Promise<Record<string, unknown>> {
  const response = await apiClient.get<ApiResponse<Record<string, unknown>>>(
    '/personalities/schema',
  )
  return unwrap(response)
}
