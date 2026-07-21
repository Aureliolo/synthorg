import { render, screen } from '@testing-library/react'
import { SetupAgentsTable } from '@/pages/setup/SetupAgentsTable'
import type { SetupAgentSummary } from '@/api/types/setup'
import type { ProviderConfig, ProviderModelConfig } from '@/api/types/providers'

function agent(overrides: Partial<SetupAgentSummary> = {}): SetupAgentSummary {
  return {
    name: 'Agent',
    role: 'Engineer',
    department: 'engineering',
    model_provider: 'prov',
    model_id: 'm-1',
    tier: 'medium',
    personality_preset: null,
    ...overrides,
  }
}

function model(id: string): ProviderModelConfig {
  return {
    id,
    alias: null,
    cost_per_1k_input: 0,
    cost_per_1k_output: 0,
    cost_per_image: null,
    max_context: 8192,
    estimated_latency_ms: null,
    local_params: null,
    metadata: {
      supports_tools: false,
      supports_vision: false,
      supports_reasoning: false,
      supports_embeddings: false,
      supports_image_generation: false,
      supports_prompt_caching: false,
      max_output_tokens: null,
      parameter_count: null,
      cost_tier: null,
      family: null,
      generation: null,
      release_date: null,
      tool_calls_verified: null,
      metadata_source: 'unknown',
    },
    stale: null,
  }
}

function provider(baseUrl: string | null): ProviderConfig {
  // The full ProviderConfig type has many credential-indicator fields; the
  // table only reads models + base_url here, so cast through unknown rather
  // than synthesising every field.
  return {
    driver: 'litellm',
    litellm_provider: 'prov',
    auth_type: 'none',
    base_url: baseUrl,
    models: [model('m-1')],
  } as unknown as ProviderConfig
}

const noop = async (): Promise<void> => {}

function renderTable(providerBaseUrl: string | null) {
  render(
    <SetupAgentsTable
      agents={[agent()]}
      providers={{ prov: provider(providerBaseUrl) }}
      personalityPresets={[]}
      onNameChange={noop}
      onModelChange={noop}
      onRandomizeName={noop}
      onPersonalityChange={noop}
    />,
  )
}

describe('SetupAgentsTable locality badge', () => {
  it('flags an agent whose model runs on a local provider', () => {
    renderTable('http://localhost:11434')
    expect(screen.getByText('local')).toBeInTheDocument()
  })

  it('does not flag an agent on a remote provider', () => {
    renderTable('https://api.example.com/v1')
    expect(screen.queryByText('local')).not.toBeInTheDocument()
  })
})
