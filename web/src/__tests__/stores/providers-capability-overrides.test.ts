import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '@/test-setup'
import { useProvidersStore } from '@/stores/providers'
import { apiError, apiSuccess } from '@/mocks/handlers/helpers'
import { ErrorCategory, ErrorCode } from '@/api/types/errors'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import type { updateModelCapabilityOverrides } from '@/api/endpoints/providers'
import type { ProviderModelResponse } from '@/api/types/providers'

/**
 * Forcing ``supports_vision`` onto the model bound to
 * ``security.vision_verify_model`` is governed: the backend rejects the
 * write with ``SECURITY_TOGGLE_CONFIRM_REQUIRED`` until the operator
 * supplies a confirm + reason. The store stages the rejected write rather
 * than toasting a generic failure, so the dashboard can collect the reason
 * and retry instead of dead-ending.
 */

const INITIAL = useProvidersStore.getState()

function resetStore(): void {
  useProvidersStore.setState(INITIAL, true)
}

const RESPONSE_MODEL: ProviderModelResponse = {
  id: 'expert-001',
  alias: null,
  capability_overrides: { supports_vision: true, supports_tools: null, supports_streaming: null, supports_embeddings: null, supports_image_generation: null, supports_reasoning: null, supports_prompt_caching: null },
  cost_per_1k_input: 0,
  cost_per_1k_output: 0,
  cost_per_image: null,
  currency: DEFAULT_CURRENCY,
  max_context: 4096,
  estimated_latency_ms: null,
  local_params: null,
  supports_tools: false,
  tool_calls_verified: null,
  supports_vision: true,
  supports_streaming: true,
  supports_embeddings: false,
  supports_reasoning: false,
  supports_image_generation: false,
  supports_prompt_caching: false,
  family: null,
  metadata_source: 'unknown',
  stale: null,
}

describe('providers store: capability-override governance', () => {
  it('stages a rejected write instead of toasting a generic failure', async () => {
    resetStore()
    server.use(
      http.patch('/api/v1/providers/:name/models/*/capabilities', () =>
        HttpResponse.json(
          apiError('Confirmation required', {
            error_code: ErrorCode.SECURITY_TOGGLE_CONFIRM_REQUIRED,
            error_category: ErrorCategory.AUTH,
          }),
          { status: 403 },
        ),
      ),
    )

    const result = await useProvidersStore
      .getState()
      .updateModelCapabilityOverrides('cloud-test', 'expert-001', {
        supports_vision: true,
        confirm: false,
        reason: '',
      })

    expect(result).toBe(false)
    expect(useProvidersStore.getState().pendingCapabilityOverridesConfirm).toEqual({
      name: 'cloud-test',
      modelId: 'expert-001',
      overrides: { supports_vision: true, confirm: false, reason: '' },
    })
  })

  it('confirming the staged write retries with confirm + reason and clears it', async () => {
    resetStore()
    useProvidersStore.setState({
      pendingCapabilityOverridesConfirm: {
        name: 'cloud-test',
        modelId: 'expert-001',
        overrides: { supports_vision: true, confirm: false, reason: '' },
      },
    })
    let receivedBody: Record<string, unknown> | null = null
    server.use(
      http.patch('/api/v1/providers/:name/models/*/capabilities', async ({ request }) => {
        receivedBody = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(
          apiSuccess<Awaited<ReturnType<typeof updateModelCapabilityOverrides>>>(
            RESPONSE_MODEL,
          ),
        )
      }),
    )

    const result = await useProvidersStore
      .getState()
      .confirmPendingCapabilityOverrides('operator confirmed this model can see')

    expect(result).toBe(true)
    expect(receivedBody).toEqual({
      supports_vision: true,
      confirm: true,
      reason: 'operator confirmed this model can see',
    })
    expect(useProvidersStore.getState().pendingCapabilityOverridesConfirm).toBeNull()
  })

  it('a blank reason falls back to a descriptive default', async () => {
    resetStore()
    useProvidersStore.setState({
      pendingCapabilityOverridesConfirm: {
        name: 'cloud-test',
        modelId: 'expert-001',
        overrides: { supports_vision: true, confirm: false, reason: '' },
      },
    })
    let receivedReason = ''
    server.use(
      http.patch('/api/v1/providers/:name/models/*/capabilities', async ({ request }) => {
        const body = (await request.json()) as { reason: string }
        receivedReason = body.reason
        return HttpResponse.json(
          apiSuccess<Awaited<ReturnType<typeof updateModelCapabilityOverrides>>>(
            RESPONSE_MODEL,
          ),
        )
      }),
    )

    await useProvidersStore.getState().confirmPendingCapabilityOverrides('   ')

    expect(receivedReason).toBe('Confirmed via the providers dashboard')
  })

  it('dismissing the staged write discards it without a network call', () => {
    resetStore()
    useProvidersStore.setState({
      pendingCapabilityOverridesConfirm: {
        name: 'cloud-test',
        modelId: 'expert-001',
        overrides: { supports_vision: true, confirm: false, reason: '' },
      },
    })

    useProvidersStore.getState().dismissPendingCapabilityOverridesConfirm()

    expect(useProvidersStore.getState().pendingCapabilityOverridesConfirm).toBeNull()
  })

  it('confirming with nothing staged is a no-op', async () => {
    resetStore()
    const result = await useProvidersStore
      .getState()
      .confirmPendingCapabilityOverrides('some reason')
    expect(result).toBe(false)
  })
})
