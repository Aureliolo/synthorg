import type { Meta, StoryObj } from '@storybook/react-vite'
import { ModelConfigDrawer } from './ModelConfigDrawer'
import { useProvidersStore } from '@/stores/providers'
import type { ProviderModelResponse } from '@/api/types/providers'

const baseModel: ProviderModelResponse = {
  id: 'test-local-7b',
  alias: 'local-7b',
  cost_per_1k_input: 0,
  cost_per_1k_output: 0,
  currency: 'USD',
  max_context: 4096,
  estimated_latency_ms: null,
  local_params: null,
  supports_tools: false,
  supports_vision: false,
  supports_streaming: true,
}

const modelWithParams: ProviderModelResponse = {
  ...baseModel,
  id: 'test-local-13b',
  alias: 'local-13b',
  local_params: {
    num_ctx: 8192,
    num_gpu_layers: 32,
    num_threads: 8,
    num_batch: 512,
    repeat_penalty: 1.1,
  },
}

const meta = {
  title: 'Providers/ModelConfigDrawer',
  component: ModelConfigDrawer,
  args: {
    providerName: 'test-provider',
    onClose: () => {},
  },
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        updateModelConfig: async () => true,
      })
      return <Story />
    },
  ],
} satisfies Meta<typeof ModelConfigDrawer>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: { model: baseModel, open: true },
}

export const AllNullParams: Story = {
  args: {
    model: {
      ...baseModel,
      local_params: {
        num_ctx: null,
        num_gpu_layers: null,
        num_threads: null,
        num_batch: null,
        repeat_penalty: null,
      },
    },
    open: true,
  },
}

export const WithExistingParams: Story = {
  args: { model: modelWithParams, open: true },
}

export const Closed: Story = {
  args: { model: baseModel, open: false },
}
