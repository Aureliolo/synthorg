import type { Meta, StoryObj } from '@storybook/react-vite'
import { fn } from 'storybook/test'
import { ModelChangeDrawer } from './ModelChangeDrawer'
import { useProvidersStore } from '@/stores/providers'
import type { ProviderWithName } from '@/utils/providers'

const SEED_PROVIDERS = [
  {
    name: 'example-provider',
    models: [
      {
        id: 'example-large-001',
        alias: 'large',
        max_context: 200000,
        cost_per_1k_input: 0,
        cost_per_1k_output: 0,
        estimated_latency_ms: null,
        local_params: null,
        metadata: {
          supports_tools: true,
          supports_vision: true,
          supports_reasoning: false,
          max_output_tokens: null,
          family: 'example-large',
          generation: 1,
          release_date: null,
          metadata_source: 'unknown',
        },
        stale: null,
      },
    ],
  },
] as unknown as ProviderWithName[]

const meta = {
  title: 'Agents/ModelChangeDrawer',
  component: ModelChangeDrawer,
  parameters: { layout: 'fullscreen' },
  args: {
    agentId: 'agent-1',
    currentProvider: 'example-provider',
    currentModelId: 'example-large-001',
    open: true,
    onClose: fn(),
  },
  decorators: [
    (Story) => {
      useProvidersStore.setState({ providers: SEED_PROVIDERS })
      return <Story />
    },
  ],
} satisfies Meta<typeof ModelChangeDrawer>

export default meta
type Story = StoryObj<typeof meta>

export const Open: Story = {}
export const Closed: Story = { args: { open: false } }
