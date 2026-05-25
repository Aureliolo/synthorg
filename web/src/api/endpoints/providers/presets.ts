import { apiClient, unwrap, unwrapVoid } from '../../client'
import type { ApiResponse } from '../../types/http'
import type {
  PresetOverride,
  PresetOverrideUpdateRequest,
} from '../../types/providers'

export async function getPresetOverride(presetName: string): Promise<PresetOverride | null> {
  const response = await apiClient.get<ApiResponse<PresetOverride | null>>(
    `/providers/presets/${encodeURIComponent(presetName)}/override`,
  )
  return unwrap<PresetOverride | null>(response)
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
