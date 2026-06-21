import type { Meta, StoryObj } from '@storybook/react-vite'
import type {
  CloudPreset,
  LocalPreset,
  ProviderConfig,
} from '@/api/types/providers'
import { PresetPickerSections } from './PresetPickerSections'

// Vendor-agnostic fixtures; project-owned Storybook code uses
// generic names per CLAUDE.md. The runtime catalog in
// src/synthorg/providers/presets.py uses real vendor names; these
// fixtures only model preset shapes and visual states.
const cloud: CloudPreset[] = [
  // Variant: subscription auth surfaced (mirrors the curated dual-auth shape).
  {
    kind: 'cloud',
    name: 'example-provider-subscription',
    display_name: 'Example Provider (Subscription)',
    description: 'Long-context inference with subscription auth',
    driver: 'litellm',
    litellm_provider: 'example-provider-subscription',
    auth_type: 'api_key',
    supported_auth_types: ['api_key', 'subscription'],
    default_base_url: null,
    requires_base_url: false,
    is_featured: true,
    prefer_live_discovery: false,
    default_models: [],
  },
  // Variant: standard API-key cloud preset.
  {
    kind: 'cloud',
    name: 'example-provider-cloud',
    display_name: 'Example Provider (Cloud)',
    description: 'Hosted API for general-purpose inference',
    driver: 'litellm',
    litellm_provider: 'example-provider-cloud',
    auth_type: 'api_key',
    supported_auth_types: ['api_key'],
    default_base_url: null,
    requires_base_url: false,
    is_featured: true,
    prefer_live_discovery: false,
    default_models: [],
  },
  // Variant: managed-cloud counterpart of a self-hosted server.
  {
    kind: 'cloud',
    name: 'example-managed-local',
    display_name: 'Example Managed Local',
    description: 'Hosted variant of a self-hosted server',
    driver: 'litellm',
    litellm_provider: 'example-adapter',
    auth_type: 'api_key',
    supported_auth_types: ['api_key'],
    default_base_url: 'https://managed.example.test',
    requires_base_url: false,
    is_featured: true,
    prefer_live_discovery: false,
    default_models: [],
  },
  // Auto-derived soft preset: surfaces the collapsible "More
  // providers via LiteLLM" branch in the story.
  {
    kind: 'cloud',
    name: 'example-soft-provider',
    display_name: 'Example Soft Provider',
    description: "Models served via LiteLLM provider 'example-soft-provider'",
    driver: 'litellm',
    litellm_provider: 'example-soft-provider',
    auth_type: 'api_key',
    supported_auth_types: ['api_key'],
    default_base_url: null,
    requires_base_url: false,
    is_featured: false,
    prefer_live_discovery: false,
    default_models: [],
  },
]

const local: LocalPreset[] = [
  // Variant: local server with full model management capabilities.
  {
    kind: 'local',
    name: 'example-local-full',
    display_name: 'Example Local (Full)',
    description: 'Local inference server with pull/delete/config',
    driver: 'litellm',
    litellm_provider: 'example-adapter',
    auth_type: 'none',
    default_base_url: 'http://localhost:11434',
    requires_base_url: true,
    is_featured: true,
    candidate_urls: ['http://localhost:11434'],
    supports_model_pull: true,
    supports_model_delete: true,
    supports_model_config: true,
  },
  // Variant: local server without management API.
  {
    kind: 'local',
    name: 'example-local-minimal',
    display_name: 'Example Local (Minimal)',
    description: 'Local inference server (no model management API)',
    driver: 'litellm',
    litellm_provider: 'example-adapter',
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

export const LocalProbeDetected: Story = {
  args: {
    presets: [...cloud, ...local],
    probeResults: {
      'example-local-full': {
        url: 'http://localhost:11434',
        model_count: 4,
        candidates_tried: 1,
      },
    },
    probing: false,
    providers: noProviders,
    ...noopHandlers,
  },
}
