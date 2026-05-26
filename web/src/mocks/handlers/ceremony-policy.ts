import { http, HttpResponse } from 'msw'
import type {
  getActiveStrategy,
  getCeremonyPolicy,
  getDepartmentCeremonyPolicy,
  getResolvedPolicy,
  updateDepartmentCeremonyPolicy,
} from '@/api/endpoints/ceremony-policy'
import type { CeremonyPolicyConfig } from '@/api/types/ceremony-policy'
import { successFor, voidSuccess } from './helpers'

const defaultPolicy: CeremonyPolicyConfig = {
  strategy: 'task_driven',
  strategy_config: {},
  velocity_calculator: 'task_driven',
  auto_transition: true,
  transition_threshold: 0.8,
}

export const ceremonyPolicyHandlers = [
  http.get('/api/v1/ceremony-policy', () =>
    HttpResponse.json(successFor<typeof getCeremonyPolicy>(defaultPolicy)),
  ),
  http.get('/api/v1/ceremony-policy/resolved', () =>
    HttpResponse.json(
      successFor<typeof getResolvedPolicy>({
        strategy: { value: 'task_driven', source: 'default' },
        strategy_config: { value: {}, source: 'default' },
        velocity_calculator: { value: 'task_driven', source: 'default' },
        auto_transition: { value: true, source: 'default' },
        transition_threshold: { value: 0.8, source: 'default' },
      }),
    ),
  ),
  http.get('/api/v1/ceremony-policy/active', () =>
    HttpResponse.json(
      successFor<typeof getActiveStrategy>({ strategy: null, sprint_id: null }),
    ),
  ),
  http.get('/api/v1/departments/:name/ceremony-policy', () =>
    HttpResponse.json(successFor<typeof getDepartmentCeremonyPolicy>(null)),
  ),
  http.put('/api/v1/departments/:name/ceremony-policy', async ({ request }) => {
    const body = (await request.json()) as CeremonyPolicyConfig
    return HttpResponse.json(
      successFor<typeof updateDepartmentCeremonyPolicy>({ ...defaultPolicy, ...body }),
    )
  }),
  http.delete('/api/v1/departments/:name/ceremony-policy', () =>
    HttpResponse.json(voidSuccess()),
  ),
]
