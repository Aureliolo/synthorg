import type { Meta, StoryObj } from '@storybook/react-vite'
import { fn } from 'storybook/test'
import { ProviderModelList } from './ProviderModelList'
import type { ProviderModelResponse } from '@/api/types/providers'
import { DEFAULT_CURRENCY } from '@/utils/currencies'

const models: ProviderModelResponse[] = [
  {
    id: 'example-large-001',
    alias: 'large',
    cost_per_1k_input: 0.015,
    cost_per_1k_output: 0.075,
    currency: DEFAULT_CURRENCY,
    max_context: 200000,
    estimated_latency_ms: 1500,
    local_params: null,
    supports_tools: true,
    supports_vision: true,
    supports_streaming: true,
    family: null,
    stale: null,
  },
  {
    id: 'example-medium-001',
    alias: 'medium',
    cost_per_1k_input: 0.003,
    cost_per_1k_output: 0.015,
    currency: DEFAULT_CURRENCY,
    max_context: 200000,
    estimated_latency_ms: 500,
    local_params: null,
    supports_tools: true,
    supports_vision: false,
    supports_streaming: true,
    family: null,
    stale: null,
  },
  {
    id: 'example-small-001',
    alias: 'small',
    cost_per_1k_input: 0.0008,
    cost_per_1k_output: 0.004,
    currency: DEFAULT_CURRENCY,
    max_context: 200000,
    estimated_latency_ms: 200,
    local_params: null,
    supports_tools: false,
    supports_vision: false,
    supports_streaming: true,
    family: null,
    stale: null,
  },
]

const meta = {
  title: 'Providers/ProviderModelList',
  component: ProviderModelList,
  tags: ['autodocs'],
  decorators: [(Story) => <div className="max-w-3xl"><Story /></div>],
} satisfies Meta<typeof ProviderModelList>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = { args: { models } }

export const AllCapabilities: Story = {
  args: {
    models: models.slice(0, 1),
  },
}

export const NoCapabilities: Story = {
  args: {
    models: [{
      id: 'test-local-001',
      alias: 'local-small',
      cost_per_1k_input: 0,
      cost_per_1k_output: 0,
      currency: DEFAULT_CURRENCY,
      max_context: 128000,
      estimated_latency_ms: null,
      local_params: null,
      supports_tools: false,
      supports_vision: false,
      supports_streaming: false,
      family: null,
      stale: null,
    }],
  },
}

export const Empty: Story = { args: { models: [] } }

export const WithDeleteActions: Story = {
  args: { models, supportsDelete: true, onDelete: fn() },
}

export const WithConfigActions: Story = {
  args: { models, supportsConfig: true, onConfigure: fn() },
}

export const WithAllActions: Story = {
  args: {
    models,
    supportsDelete: true,
    supportsConfig: true,
    onDelete: fn(),
    onConfigure: fn(),
  },
}
