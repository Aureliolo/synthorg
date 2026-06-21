import type { Meta, StoryObj } from '@storybook/react'
import type { CloudPreset } from '@/api/types/providers'
import { ProviderFormModal } from './ProviderFormModal'
import type { ProviderFormOverrides } from './provider-form-helpers'

const examplePreset: CloudPreset = {
  kind: 'cloud',
  name: 'example-provider',
  display_name: 'Example Provider',
  description: 'Example cloud models',
  driver: 'litellm',
  litellm_provider: 'example-provider',
  auth_type: 'api_key',
  supported_auth_types: ['api_key', 'subscription', 'oauth', 'custom_header'],
  default_base_url: null,
  requires_base_url: false,
  is_featured: true,
  prefer_live_discovery: false,
  default_models: [],
}

const baseOverrides: ProviderFormOverrides = {
  presets: [examplePreset],
  presetsLoading: false,
  presetsError: null,
  onFetchPresets: () => {},
  onCreateFromPreset: () => Promise.resolve(null),
  onCreateProvider: () => Promise.resolve(null),
}

const meta = {
  title: 'Providers/ProviderFormModal',
  component: ProviderFormModal,
  parameters: { layout: 'fullscreen' },
} satisfies Meta<typeof ProviderFormModal>

export default meta
type Story = StoryObj<typeof meta>

export const CreateFromPreset: Story = {
  args: {
    open: true,
    onClose: () => {},
    mode: 'create',
    initialPreset: 'example-provider',
    overrides: baseOverrides,
  },
}

export const CustomEndpoint: Story = {
  args: {
    open: true,
    onClose: () => {},
    mode: 'create',
    initialPreset: null,
    overrides: baseOverrides,
  },
}

export const PresetsLoading: Story = {
  args: {
    open: true,
    onClose: () => {},
    mode: 'create',
    initialPreset: null,
    overrides: { ...baseOverrides, presetsLoading: true, presets: [] },
  },
}

export const PresetsError: Story = {
  args: {
    open: true,
    onClose: () => {},
    mode: 'create',
    initialPreset: null,
    overrides: { ...baseOverrides, presetsError: 'Could not reach the API', presets: [] },
  },
}

export const SubmitError: Story = {
  args: {
    open: true,
    onClose: () => {},
    mode: 'create',
    initialPreset: 'example-provider',
    overrides: { ...baseOverrides, submitError: 'A provider with that name already exists.' },
  },
}
