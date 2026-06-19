import type { Meta, StoryObj } from '@storybook/react-vite'
import { fn } from 'storybook/test'
import { AgentModelPicker } from './AgentModelPicker'
import type { ProviderConfig, ProviderModelResponse } from '@/api/types/providers'
import { DEFAULT_CURRENCY } from '@/utils/currencies'

function model(
  id: string,
  overrides: Partial<ProviderModelResponse> = {},
): ProviderModelResponse {
  return {
    id,
    alias: null,
    cost_per_1k_input: 0,
    cost_per_1k_output: 0,
    currency: DEFAULT_CURRENCY,
    max_context: 200000,
    estimated_latency_ms: null,
    local_params: null,
    supports_tools: false,
    supports_vision: false,
    supports_streaming: true,
    family: null,
    stale: null,
    ...overrides,
  }
}

function providersWith(models: ProviderModelResponse[]): Record<string, ProviderConfig> {
  return { 'example-provider': { models } as unknown as ProviderConfig }
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
      model('example-large-001', {
        alias: 'large',
        family: 'example-large',
        supports_tools: true,
        supports_vision: true,
      }),
      model('example-large-002', { family: 'example-large', supports_tools: true }),
      model('example-small-001', { family: 'example-small', max_context: 32000 }),
    ]),
  },
}

export const WithStaleModel: Story = {
  args: {
    providers: providersWith([
      model('example-large-002', { family: 'example-large' }),
      model('example-large-001', {
        family: 'example-large',
        stale: {
          reason: 'removed_from_catalog',
          flagged_at: '2026-06-01T12:00:00+00:00',
          last_seen: null,
          successor_model_id: 'example-large-002',
        },
      }),
    ]),
  },
}

export const WithSelectedModel: Story = {
  args: {
    currentModelId: 'example-large-001',
    providers: providersWith([
      model('example-large-001', {
        alias: 'large',
        family: 'example-large',
        supports_tools: true,
        supports_vision: true,
      }),
      model('example-small-001', { family: 'example-small', max_context: 32000 }),
    ]),
  },
}

export const NoModels: Story = { args: { providers: {} } }
export const Disabled: Story = {
  args: {
    disabled: true,
    providers: providersWith([model('example-large-001', { family: 'example-large' })]),
  },
}
