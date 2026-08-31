import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ProviderModelList } from '@/pages/providers/ProviderModelList'
import type { ProviderModelResponse } from '@/api/types/providers'
import { DEFAULT_CURRENCY } from '@/utils/currencies'

function buildModel(
  overrides: Partial<ProviderModelResponse> = {},
): ProviderModelResponse {
  return {
    id: 'plain-model',
    alias: null,
    capability_overrides: null,
    cost_per_1k_input: 0,
    cost_per_1k_output: 0,
    cost_per_image: null,
    currency: DEFAULT_CURRENCY,
    max_context: 200000,
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
    ...overrides,
  }
}

describe('ProviderModelList capability provenance', () => {
  it('marks an unknown-source model with no capabilities as unverified', () => {
    render(<ProviderModelList models={[buildModel({ metadata_source: 'unknown' })]} />)
    expect(screen.getByText('capabilities unverified')).toBeInTheDocument()
  })

  it('shows no unverified pill for a known-source model with no capabilities', () => {
    // A LiteLLM-sourced plain chat model genuinely has no extra capabilities;
    // it must not be labelled "unverified".
    render(<ProviderModelList models={[buildModel({ metadata_source: 'litellm' })]} />)
    expect(screen.queryByText('capabilities unverified')).not.toBeInTheDocument()
  })

  it('shows capability badges (not the unverified pill) when flags are set', () => {
    render(
      <ProviderModelList
        models={[buildModel({ metadata_source: 'unknown', supports_tools: true })]}
      />,
    )
    expect(screen.getByText('tools')).toBeInTheDocument()
    expect(screen.queryByText('capabilities unverified')).not.toBeInTheDocument()
  })

  it('shows a cached pill when prompt caching is supported', () => {
    render(
      <ProviderModelList
        models={[buildModel({ metadata_source: 'litellm', supports_prompt_caching: true })]}
      />,
    )
    expect(screen.getByText('cached')).toBeInTheDocument()
  })

  it('marks an operator-overridden capability, not an auto-detected one', () => {
    render(
      <ProviderModelList
        models={[
          buildModel({
            metadata_source: 'litellm',
            supports_prompt_caching: true,
            supports_tools: true,
            capability_overrides: {
              supports_tools: null,
              supports_vision: null,
              supports_streaming: null,
              supports_embeddings: null,
              supports_image_generation: null,
              supports_reasoning: null,
              supports_prompt_caching: true,
            },
          }),
        ]}
      />,
    )
    expect(screen.getByTitle('cached: set by an operator override')).toBeInTheDocument()
    expect(screen.getByText('tools')).not.toHaveAttribute('title')
  })
})
