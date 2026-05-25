import { http, HttpResponse } from 'msw'
import type {
  getPresetOverride,
  updatePresetOverride,
} from '@/api/endpoints/providers'
import type { PresetOverride } from '@/api/types/providers'
import { successFor, voidSuccess } from '../helpers'

export function buildPresetOverride(
  overrides: Partial<PresetOverride> = {},
): PresetOverride {
  return {
    preset_name: 'preset-default',
    default_models: null,
    supported_auth_types: null,
    candidate_urls: null,
    base_url: null,
    updated_at: '2026-04-28T00:00:00+00:00',
    updated_by: 'test-actor',
    ...overrides,
  }
}

export const presetsHandlers = [
  http.get('/api/v1/providers/presets/:presetName/override', () =>
    HttpResponse.json(successFor<typeof getPresetOverride>(null)),
  ),
  http.patch('/api/v1/providers/presets/:presetName/override', ({ params }) =>
    HttpResponse.json(
      successFor<typeof updatePresetOverride>({
        preset_name: String(params.presetName),
        default_models: null,
        supported_auth_types: null,
        candidate_urls: null,
        base_url: null,
        updated_at: '2026-04-28T00:00:00+00:00',
        updated_by: 'test-actor',
      }),
    ),
  ),
  http.delete('/api/v1/providers/presets/:presetName/override', () =>
    HttpResponse.json(voidSuccess()),
  ),
]
