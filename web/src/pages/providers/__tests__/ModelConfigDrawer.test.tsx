import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, it, expect } from 'vitest'
import { successFor } from '@/mocks/handlers'
import { server } from '@/test-setup'
import { ModelConfigDrawer } from '../ModelConfigDrawer'
import type { updateModelCapabilityOverrides } from '@/api/endpoints/providers'
import type {
  CapabilityOverridesUpdateRequest,
  ModelCapabilityOverrides,
  ProviderModelResponse,
} from '@/api/types/providers'
import { DEFAULT_CURRENCY } from '@/utils/currencies'

const baseModel: ProviderModelResponse = {
  id: 'test-basic-001',
  alias: null,
  capability_overrides: null,
  cost_per_1k_input: 0,
  cost_per_1k_output: 0,
  cost_per_image: null,
  currency: DEFAULT_CURRENCY,
  max_context: 4096,
  estimated_latency_ms: null,
  local_params: null,
  supports_tools: false,
  tool_calls_verified: null,
  supports_vision: false,
  supports_streaming: true,
  supports_embeddings: false,
  supports_reasoning: false,
  supports_image_generation: false,
  supports_prompt_caching: false,
  family: null,
  metadata_source: 'unknown',
  stale: null,
}

describe('ModelConfigDrawer', () => {
  it('hides the local-params section for a provider with no local runtime', () => {
    render(
      <ModelConfigDrawer
        providerName="test-provider"
        model={baseModel}
        open
        onClose={() => {}}
        supportsLocalParams={false}
      />,
    )
    expect(screen.queryByLabelText(/Context window/)).not.toBeInTheDocument()
    expect(screen.getByText('Capability overrides')).toBeInTheDocument()
  })

  it('shows the local-params section for a provider with a local runtime', () => {
    render(
      <ModelConfigDrawer
        providerName="test-provider"
        model={baseModel}
        open
        onClose={() => {}}
        supportsLocalParams={true}
      />,
    )
    expect(screen.getByLabelText(/Context window/)).toBeInTheDocument()
  })

  it('pre-populates a select from an existing override', () => {
    render(
      <ModelConfigDrawer
        providerName="test-provider"
        model={{
          ...baseModel,
          capability_overrides: {
            supports_tools: true,
            supports_vision: null,
            supports_streaming: null,
            supports_embeddings: null,
            supports_image_generation: null,
            supports_reasoning: null,
            supports_prompt_caching: false,
          },
        }}
        open
        onClose={() => {}}
        supportsLocalParams={false}
      />,
    )
    expect(screen.getByLabelText(/Tool calling/)).toHaveValue('true')
    expect(screen.getByLabelText(/Prompt caching/)).toHaveValue('false')
    expect(screen.getByLabelText(/^Vision/)).toHaveValue('')
  })

  it('PATCHes the correct model id with the full override state', async () => {
    let receivedUrl = ''
    let receivedBody: CapabilityOverridesUpdateRequest | null = null
    server.use(
      http.patch(
        '/api/v1/providers/:name/models/*/capabilities',
        async ({ request }) => {
          // The drawer always sends every field explicitly (never omits
          // one), so the request body is safely a full ModelCapabilityOverrides.
          const overrides = (await request.json()) as ModelCapabilityOverrides
          receivedUrl = request.url
          receivedBody = overrides
          return HttpResponse.json(
            successFor<typeof updateModelCapabilityOverrides>({
              ...baseModel,
              supports_vision: true,
              capability_overrides: overrides,
            }),
          )
        },
      ),
    )
    render(
      <ModelConfigDrawer
        providerName="test-provider"
        model={baseModel}
        open
        onClose={() => {}}
        supportsLocalParams={false}
      />,
    )
    fireEvent.change(screen.getByLabelText(/Vision/), { target: { value: 'true' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save overrides' }))

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Save overrides' })).toBeEnabled(),
    )
    expect(receivedUrl).toContain(`/${baseModel.id}/capabilities`)
    expect(receivedBody).toEqual({
      supports_tools: null,
      supports_vision: true,
      supports_streaming: null,
      supports_embeddings: null,
      supports_image_generation: null,
      supports_reasoning: null,
      supports_prompt_caching: null,
    })
  })
})
