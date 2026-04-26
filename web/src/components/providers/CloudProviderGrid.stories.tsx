import type { Meta, StoryObj } from '@storybook/react-vite'
import type { CloudPreset } from '@/api/types/providers'
import { CloudProviderGrid } from './CloudProviderGrid'

const samplePresets: CloudPreset[] = [
  {
    kind: 'cloud',
    name: 'anthropic',
    display_name: 'Anthropic',
    description: 'Claude models (Opus, Sonnet, Haiku)',
    driver: 'litellm',
    litellm_provider: 'anthropic',
    auth_type: 'api_key',
    supported_auth_types: ['api_key', 'subscription'],
    default_base_url: null,
    requires_base_url: false,
    default_models: [],
  },
  {
    kind: 'cloud',
    name: 'openai',
    display_name: 'OpenAI',
    description: 'GPT and o-series models',
    driver: 'litellm',
    litellm_provider: 'openai',
    auth_type: 'api_key',
    supported_auth_types: ['api_key'],
    default_base_url: null,
    requires_base_url: false,
    default_models: [],
  },
  {
    kind: 'cloud',
    name: 'gemini',
    display_name: 'Google AI Studio',
    description: 'Gemini models via Google AI',
    driver: 'litellm',
    litellm_provider: 'gemini',
    auth_type: 'api_key',
    supported_auth_types: ['api_key'],
    default_base_url: null,
    requires_base_url: false,
    default_models: [],
  },
  {
    kind: 'cloud',
    name: 'ollama-cloud',
    display_name: 'Ollama Cloud',
    description: 'Hosted Ollama models (managed inference)',
    driver: 'litellm',
    litellm_provider: 'ollama',
    auth_type: 'api_key',
    supported_auth_types: ['api_key'],
    default_base_url: 'https://ollama.com',
    requires_base_url: false,
    default_models: [],
  },
]

const meta = {
  title: 'Providers/CloudProviderGrid',
  component: CloudProviderGrid,
  parameters: { layout: 'padded' },
} satisfies Meta<typeof CloudProviderGrid>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    presets: samplePresets,
    onSelect: (name) => alert(`Picked ${name}`),
  },
}

export const SomeConfigured: Story = {
  args: {
    presets: samplePresets,
    addedPresets: new Set(['anthropic', 'gemini']),
    onSelect: (name) => alert(`Picked ${name}`),
  },
}
