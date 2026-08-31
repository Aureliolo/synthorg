import type { Meta, StoryObj } from '@storybook/react-vite'
import { ModelConfigDrawer } from './ModelConfigDrawer'
import { useProvidersStore } from '@/stores/providers'
import type { ProviderModelResponse } from '@/api/types/providers'
import { DEFAULT_CURRENCY } from '@/utils/currencies'

const baseModel: ProviderModelResponse = {
  id: 'test-local-7b',
  alias: 'local-7b',
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
    supportsLocalParams: true,
  },
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        updateModelConfig: () => Promise.resolve(true),
        updateModelCapabilityOverrides: () => Promise.resolve(true),
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

export const WithCapabilityOverrides: Story = {
  args: {
    model: {
      ...baseModel,
      supports_tools: true,
      capability_overrides: {
        supports_tools: true,
        supports_vision: null,
        supports_streaming: null,
        supports_embeddings: null,
        supports_image_generation: null,
        supports_reasoning: null,
        supports_prompt_caching: false,
      },
    },
    open: true,
  },
}

export const NoLocalParamsSupport: Story = {
  // A cloud provider has no local runtime to tune, but capability overrides
  // still apply: the local-params section is hidden, the overrides section
  // is not.
  args: { model: baseModel, open: true, supportsLocalParams: false },
}
