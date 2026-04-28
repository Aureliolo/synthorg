import type { Meta, StoryObj } from '@storybook/react-vite'
import { SyncModelsConfirmDialog } from './SyncModelsConfirmDialog'
import { useProvidersStore } from '@/stores/providers'

const meta = {
  title: 'Providers/SyncModelsConfirmDialog',
  component: SyncModelsConfirmDialog,
  args: {
    providerName: 'test-provider',
    open: true,
    onClose: () => {},
  },
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        syncProviderModels: async () => ({
          added: ['example-large-001', 'example-medium-001'],
          removed: [],
          updated: ['example-small-001'],
          models: [],
        }),
      })
      return <Story />
    },
  ],
} satisfies Meta<typeof SyncModelsConfirmDialog>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const WithPresetHint: Story = {
  args: { presetHint: 'ollama' },
}

export const Closed: Story = { args: { open: false } }
