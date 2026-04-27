import type { Meta, StoryObj } from '@storybook/react-vite'
import type {
  CloudPreset,
  LocalPreset,
  ProviderConfig,
} from '@/api/types/providers'
import { PresetPickerSections } from './PresetPickerSections'

const cloud: CloudPreset[] = [
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
    is_featured: true,
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
    is_featured: true,
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
    is_featured: true,
    default_models: [],
  },
]

const local: LocalPreset[] = [
  {
    kind: 'local',
    name: 'ollama',
    display_name: 'Ollama',
    description: 'Local Ollama inference server',
    driver: 'litellm',
    litellm_provider: 'ollama',
    auth_type: 'none',
    default_base_url: 'http://localhost:11434',
    requires_base_url: true,
    is_featured: true,
    candidate_urls: ['http://localhost:11434'],
    supports_model_pull: true,
    supports_model_delete: true,
    supports_model_config: true,
  },
  {
    kind: 'local',
    name: 'lm-studio',
    display_name: 'LM Studio',
    description: 'Local LLM development environment',
    driver: 'litellm',
    litellm_provider: 'openai',
    auth_type: 'none',
    default_base_url: 'http://localhost:1234/v1',
    requires_base_url: true,
    is_featured: true,
    candidate_urls: ['http://localhost:1234/v1'],
    supports_model_pull: false,
    supports_model_delete: false,
    supports_model_config: false,
  },
]

const noProviders: Record<string, ProviderConfig> = {}

const meta = {
  title: 'Providers/PresetPickerSections',
  component: PresetPickerSections,
  parameters: { layout: 'padded' },
} satisfies Meta<typeof PresetPickerSections>

export default meta
type Story = StoryObj<typeof meta>

const noopHandlers = {
  onSelectCloud: (name: string) => alert(`Cloud preset clicked: ${name}`),
  onAddLocal: (name: string, url: string) => alert(`Add local ${name} at ${url}`),
  onAddCloudCounterpart: (name: string) => alert(`Add cloud counterpart: ${name}`),
  onReprobe: () => alert('Re-scan'),
  onConfigureManually: () => alert('Configure manually'),
}

export const Default: Story = {
  args: {
    presets: [...cloud, ...local],
    probeResults: {},
    probing: false,
    providers: noProviders,
    ...noopHandlers,
  },
}

export const ProbingNoResultsYet: Story = {
  args: {
    presets: [...cloud, ...local],
    probeResults: {},
    probing: true,
    providers: noProviders,
    ...noopHandlers,
  },
}

export const OllamaDetected: Story = {
  args: {
    presets: [...cloud, ...local],
    probeResults: {
      ollama: { url: 'http://localhost:11434', model_count: 4, candidates_tried: 1 },
    },
    probing: false,
    providers: noProviders,
    ...noopHandlers,
  },
}
