import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AgentModelPicker } from '@/components/ui/agent-model-picker'
import type {
  ProviderConfig,
  ProviderModelConfig,
} from '@/api/types/providers'

function buildConfigModel(
  id: string,
  toolCallsVerified: boolean | null,
): ProviderModelConfig {
  return {
    id,
    alias: null,
    cost_per_1k_input: 0,
    cost_per_1k_output: 0,
    max_context: 200000,
    estimated_latency_ms: null,
    local_params: null,
    metadata: {
      supports_tools: true,
      tool_calls_verified: toolCallsVerified,
      supports_vision: false,
      supports_reasoning: false,
      max_output_tokens: null,
      parameter_count: null,
      cost_tier: null,
      family: null,
      generation: null,
      release_date: null,
      metadata_source: 'unknown',
    },
    stale: null,
  }
}

function providersWith(models: ProviderModelConfig[]): Record<string, ProviderConfig> {
  return { 'example-provider': { models } as unknown as ProviderConfig }
}

describe('AgentModelPicker tool-calling hint', () => {
  it('shows a "no tools" hint for a runtime-downgraded model', () => {
    render(
      <AgentModelPicker
        currentProvider=""
        currentModelId=""
        providers={providersWith([buildConfigModel('downgraded', false)])}
        onChange={vi.fn()}
      />,
    )
    expect(screen.getByRole('option', { name: /no tools/ })).toBeInTheDocument()
  })

  it('omits the hint for a model that has not been downgraded', () => {
    render(
      <AgentModelPicker
        currentProvider=""
        currentModelId=""
        providers={providersWith([buildConfigModel('healthy', null)])}
        onChange={vi.fn()}
      />,
    )
    expect(screen.queryByRole('option', { name: /no tools/ })).not.toBeInTheDocument()
  })
})
