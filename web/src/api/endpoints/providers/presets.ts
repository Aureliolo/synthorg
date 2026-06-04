import { apiClient, ApiRequestError, unwrap, unwrapVoid } from '../../client'
import type {
  ApiResponse,
  PresetOverride,
  PresetOverrideUpdateRequest,
} from '@/api/types'

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
  // Axios types ``response.data`` as the declared envelope, but the server
  // can return a malformed / empty body at runtime; widen the boundary so the
  // guards below are real, not dead.
  const body = response.data as ApiResponse<PresetOverride | null> | null | undefined
  if (!body || typeof body !== 'object') {
    throw new ApiRequestError('Unknown API error')
  }
  if (!body.success) {
    const detail = 'error_detail' in body ? body.error_detail : null
    throw new ApiRequestError(body.error ?? 'Unknown API error', detail)
  }
  // Distinguish "absent override" (``data: null``, intentional) from a
  // malformed envelope that omits ``data`` entirely. Coalescing
  // ``undefined`` to ``null`` would mask a backend bug as the
  // happy-not-found path; insist on explicit ``data`` presence and
  // surface anything else as an ``ApiRequestError`` so the operator
  // sees the wire-contract violation instead of a silent miss.
  if (!('data' in body)) {
    throw new ApiRequestError('Invalid API response: ``data`` field missing')
  }
  return body.data
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
