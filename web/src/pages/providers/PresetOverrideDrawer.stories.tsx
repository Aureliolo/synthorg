import type { Meta, StoryObj } from '@storybook/react-vite'
import { PresetOverrideDrawer } from './PresetOverrideDrawer'
import { useProvidersStore } from '@/stores/providers'
import type {
  CloudPreset as CloudPresetType,
  LocalPreset as LocalPresetType,
} from '@/api/types/providers'

const cloudPreset: CloudPresetType = {
  kind: 'cloud',
  name: 'cloud-test',
  display_name: 'Cloud Test Provider',
  description: 'Hosted LLM provider used in stories',
  driver: 'litellm',
  litellm_provider: 'cloud-test',
  auth_type: 'api_key',
  supported_auth_types: ['api_key'],
  default_base_url: 'https://api.example.com/v1',
  requires_base_url: false,
  default_models: [],
  is_featured: false,
  prefer_live_discovery: false,
}

const localPreset: LocalPresetType = {
  kind: 'local',
  name: 'local-test',
  display_name: 'Local Test Provider',
  description: 'Self-hosted local provider used in stories',
  driver: 'litellm',
  litellm_provider: 'local-test',
  auth_type: 'none',
  default_base_url: null,
  requires_base_url: true,
  candidate_urls: ['http://localhost:11434'],
  supports_model_pull: true,
  supports_model_delete: true,
  supports_model_config: true,
  is_featured: false,
}

const meta = {
  title: 'Providers/PresetOverrideDrawer',
  component: PresetOverrideDrawer,
  args: {
    open: true,
    onClose: () => {},
  },
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        presetOverride: null,
        presetOverrideLoading: false,
        presetOverrideError: null,
        fetchPresetOverride: () => Promise.resolve(),
        updatePresetOverride: () => Promise.resolve(null),
        deletePresetOverride: () => Promise.resolve(true),
      })
      return <Story />
    },
  ],
} satisfies Meta<typeof PresetOverrideDrawer>

export default meta
type Story = StoryObj<typeof meta>

export const CloudPresetEmpty: Story = {
  args: { preset: cloudPreset },
}

export const CloudPresetWithOverride: Story = {
  args: { preset: cloudPreset },
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        presetOverride: {
          preset_name: 'cloud-test',
          default_models: null,
          supported_auth_types: null,
          candidate_urls: null,
          base_url: 'https://api.override.example.com/v1',
          updated_at: '2026-04-28T08:00:00+00:00',
          updated_by: 'user-42',
        },
      })
      return <Story />
    },
  ],
}

export const LocalPreset: Story = {
  args: { preset: localPreset },
}

export const LocalPresetWithCandidates: Story = {
  args: { preset: localPreset },
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        presetOverride: {
          preset_name: 'local-test',
          default_models: null,
          supported_auth_types: null,
          candidate_urls: ['http://localhost:11434', 'http://10.0.0.5:11434'],
          base_url: null,
          updated_at: '2026-04-28T08:00:00+00:00',
          updated_by: 'user-42',
        },
      })
      return <Story />
    },
  ],
}

export const Loading: Story = {
  args: { preset: cloudPreset },
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        presetOverride: null,
        presetOverrideLoading: true,
      })
      return <Story />
    },
  ],
}

export const ErrorState: Story = {
  args: { preset: cloudPreset },
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        presetOverride: null,
        presetOverrideLoading: false,
        presetOverrideError: 'Could not reach backend',
      })
      return <Story />
    },
  ],
}
