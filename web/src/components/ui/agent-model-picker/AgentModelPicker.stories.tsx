import type { Meta, StoryObj } from '@storybook/react-vite'
import { fn } from 'storybook/test'
import { AgentModelPicker } from './AgentModelPicker'
import type { ProviderConfig, ProviderModelConfig } from '@/api/types/providers'

/**
 * The component reads capability fields off ``ProviderModelConfig.metadata``
 * (the unflattened wire shape), not the dashboard's flattened
 * ``ProviderModelResponse`` display shape, so a fixture built from the
 * latter would leave ``model.metadata`` undefined at render. This
 * convenience shape covers only what these stories exercise.
 */
interface ModelFixtureOverrides {
  alias?: string
  family?: string
  supports_tools?: boolean
  supports_vision?: boolean
  supports_embeddings?: boolean
  tool_calls_verified?: boolean | null
  max_context?: number
  stale?: ProviderModelConfig['stale']
}

const METADATA_DEFAULTS = {
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
} as const

function model(id: string, overrides: ModelFixtureOverrides = {}): ProviderModelConfig {
  const { alias, max_context, stale, ...metadataOverrides } = overrides
  return {
    id,
    alias: alias ?? null,
    capability_overrides: null,
    cost_per_1k_input: 0,
    cost_per_1k_output: 0,
    cost_per_image: null,
    max_context: max_context ?? 200000,
    estimated_latency_ms: null,
    local_params: null,
    metadata: { ...METADATA_DEFAULTS, ...metadataOverrides },
    stale: stale ?? null,
  }
}

function providersWith(models: ProviderModelConfig[]): Record<string, ProviderConfig> {
  const provider: ProviderConfig = {
    name: 'example-provider',
    driver: 'litellm',
    litellm_provider: 'example-provider',
    auth_type: 'api_key',
    agent_eligible: true,
    billing_model: 'per_token',
    base_url: null,
    keep_alive: null,
    models,
    has_api_key: true,
    has_oauth_credentials: false,
    has_custom_header: false,
    has_subscription_token: false,
    tos_accepted_at: null,
    oauth_token_url: null,
    oauth_client_id: null,
    oauth_scope: null,
    custom_header_name: null,
    preset_name: null,
    supports_model_pull: false,
    supports_model_delete: false,
    supports_model_config: false,
  }
  return { 'example-provider': provider }
}

const meta = {
  title: 'UI/AgentModelPicker',
  component: AgentModelPicker,
  parameters: { layout: 'padded' },
  args: { onChange: fn(), currentProvider: 'example-provider', currentModelId: '' },
} satisfies Meta<typeof AgentModelPicker>

export default meta
type Story = StoryObj<typeof meta>

export const GroupedByFamily: Story = {
  args: {
    providers: providersWith([
      model('example-expert-001', {
        alias: 'large',
        family: 'example-expert',
        supports_tools: true,
        supports_vision: true,
      }),
      model('example-expert-002', { family: 'example-expert', supports_tools: true }),
      model('example-basic-001', { family: 'example-basic', max_context: 32000 }),
    ]),
  },
}

export const WithStaleModel: Story = {
  args: {
    providers: providersWith([
      model('example-expert-002', { family: 'example-expert' }),
      model('example-expert-001', {
        family: 'example-expert',
        stale: {
          reason: 'removed_from_catalog',
          flagged_at: '2026-06-01T12:00:00+00:00',
          last_seen: null,
          successor_model_id: 'example-expert-002',
        },
      }),
    ]),
  },
}

export const WithSelectedModel: Story = {
  args: {
    currentModelId: 'example-expert-001',
    providers: providersWith([
      model('example-expert-001', {
        alias: 'large',
        family: 'example-expert',
        supports_tools: true,
        supports_vision: true,
      }),
      model('example-basic-001', { family: 'example-basic', max_context: 32000 }),
    ]),
  },
}

export const WithToolCallingDowngraded: Story = {
  args: {
    providers: providersWith([
      model('example-expert-001', {
        alias: 'large',
        family: 'example-expert',
        supports_tools: true,
        // Runtime feedback proved it cannot call tools: the hint shows
        // 'no tools' instead of the contradictory 'tools'.
        tool_calls_verified: false,
      }),
      model('example-expert-002', { family: 'example-expert', supports_tools: true }),
    ]),
  },
}

export const ChatKind: Story = {
  args: {
    kind: 'chat',
    providers: providersWith([
      model('example-expert-001', { family: 'example-expert', supports_tools: true }),
      // Offered by the same provider and deliberately absent from this story's
      // list: an embedding model cannot hold a conversation.
      model('example-embed-001', { family: 'example-embed', supports_embeddings: true }),
    ]),
  },
}

export const EmbeddingKind: Story = {
  args: {
    kind: 'embedding',
    providers: providersWith([
      model('example-embed-001', {
        alias: 'embed',
        family: 'example-embed',
        supports_embeddings: true,
      }),
      model('example-embed-002', { family: 'example-embed', supports_embeddings: true }),
      model('example-expert-001', { family: 'example-expert', supports_tools: true }),
    ]),
  },
}

export const NoModels: Story = { args: { providers: {} } }
export const Disabled: Story = {
  args: {
    disabled: true,
    providers: providersWith([model('example-expert-001', { family: 'example-expert' })]),
  },
}
