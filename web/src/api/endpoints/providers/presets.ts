import { apiClient, ApiRequestError, unwrap, unwrapVoid } from '../../client'
import type { ErrorDetail } from '../../types/errors'
import type { ApiResponse } from '../../types/http'
import type {
  PresetOverride,
  PresetOverrideUpdateRequest,
} from '../../types/providers'

export async function getPresetOverride(presetName: string): Promise<PresetOverride | null> {
  const response = await apiClient.get<ApiResponse<PresetOverride | null>>(
    `/providers/presets/${encodeURIComponent(presetName)}/override`,
  )
  // ``unwrap`` throws when ``data`` is ``null`` because most callers
  // treat null as "missing required entity". The override endpoint
  // intentionally returns ``{ success: true, data: null }`` when no
  // override exists for the preset, so the absence is a normal value
  // here, not an error. Inspect the envelope directly and pass null
  // through; otherwise the ``| null`` half of the return type would be
  // unreachable.
  const body = response.data
  if (!body || typeof body !== 'object') {
    throw new ApiRequestError('Unknown API error')
  }
  if (!body.success) {
    const detail = 'error_detail' in body ? (body.error_detail as ErrorDetail | null) : null
    throw new ApiRequestError(body.error ?? 'Unknown API error', detail)
  }
  return body.data ?? null
}

export async function updatePresetOverride(
  presetName: string,
  data: PresetOverrideUpdateRequest,
): Promise<PresetOverride> {
  const response = await apiClient.patch<ApiResponse<PresetOverride>>(
    `/providers/presets/${encodeURIComponent(presetName)}/override`,
    data,
  )
  return unwrap(response)
}

export async function deletePresetOverride(presetName: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(
    `/providers/presets/${encodeURIComponent(presetName)}/override`,
  )
  unwrapVoid(response)
}
