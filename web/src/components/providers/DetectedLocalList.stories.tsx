import type { Meta, StoryObj } from '@storybook/react-vite'
import type { LocalPreset, ProviderConfig } from '@/api/types/providers'
import { DetectedLocalList } from './DetectedLocalList'

const ollama: LocalPreset = {
  kind: 'local',
  name: 'ollama',
  display_name: 'Ollama',
  description: 'Local Ollama inference server',
  driver: 'litellm',
  litellm_provider: 'ollama',
  auth_type: 'none',
  default_base_url: 'http://localhost:11434',
  requires_base_url: true,
  candidate_urls: ['http://localhost:11434'],
  supports_model_pull: true,
  supports_model_delete: true,
  supports_model_config: true,
}

const lmStudio: LocalPreset = {
  kind: 'local',
  name: 'lm-studio',
  display_name: 'LM Studio',
  description: 'Local LLM development environment',
  driver: 'litellm',
  litellm_provider: 'openai',
  auth_type: 'none',
  default_base_url: 'http://localhost:1234/v1',
  requires_base_url: true,
  candidate_urls: ['http://localhost:1234/v1'],
  supports_model_pull: false,
  supports_model_delete: false,
  supports_model_config: false,
}

const noProviders: Record<string, ProviderConfig> = {}

const meta = {
  title: 'Providers/DetectedLocalList',
  component: DetectedLocalList,
  parameters: { layout: 'padded' },
} satisfies Meta<typeof DetectedLocalList>

export default meta
type Story = StoryObj<typeof meta>

export const NothingDetectedReturnsNull: Story = {
  args: {
    localPresets: [ollama, lmStudio],
    probeResults: {},
    probing: false,
    providers: noProviders,
    onAddLocal: (name, url) => alert(`Add local ${name} at ${url}`),
    onAddCloud: (name) => alert(`Add cloud ${name}`),
    onReprobe: () => alert('Re-scan'),
  },
  parameters: {
    docs: {
      description: {
        story: 'When nothing is detected and the probe is idle, the entire panel is suppressed. This story will render nothing -- that is the desired behaviour.',
      },
    },
  },
}

export const ProbingInFlight: Story = {
  args: {
    localPresets: [ollama, lmStudio],
    probeResults: {},
    probing: true,
    providers: noProviders,
    onAddLocal: () => undefined,
    onAddCloud: () => undefined,
    onReprobe: () => undefined,
  },
}

export const OllamaDetected: Story = {
  args: {
    localPresets: [ollama, lmStudio],
    probeResults: {
      ollama: { url: 'http://localhost:11434', model_count: 4, candidates_tried: 1 },
    },
    probing: false,
    providers: noProviders,
    onAddLocal: (name, url) => alert(`Add local ${name} at ${url}`),
    onAddCloud: (name) => alert(`Add cloud ${name}`),
    onReprobe: () => alert('Re-scan'),
  },
}

export const BothDetectedAndCloudAlreadyAdded: Story = {
  args: {
    localPresets: [ollama, lmStudio],
    probeResults: {
      ollama: { url: 'http://localhost:11434', model_count: 4, candidates_tried: 1 },
      'lm-studio': { url: 'http://localhost:1234/v1', model_count: 2, candidates_tried: 1 },
    },
    probing: false,
    providers: {
      'ollama-cloud': {} as ProviderConfig,
    },
    onAddLocal: (name, url) => alert(`Add local ${name} at ${url}`),
    onAddCloud: (name) => alert(`Add cloud ${name}`),
    onReprobe: () => alert('Re-scan'),
  },
}
