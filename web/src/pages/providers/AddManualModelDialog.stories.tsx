import type { Meta, StoryObj } from '@storybook/react-vite'
import { AddManualModelDialog } from './AddManualModelDialog'
import { useProvidersStore } from '@/stores/providers'

const meta = {
  title: 'Providers/AddManualModelDialog',
  component: AddManualModelDialog,
  args: {
    providerName: 'test-provider',
    open: true,
    onClose: () => {},
  },
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        addProviderModel: () => Promise.resolve({
          name: null,
          driver: 'litellm',
          litellm_provider: null,
          auth_type: 'api_key',
          base_url: null,
          models: [],
          has_api_key: true,
          has_oauth_credentials: false,
          has_custom_header: false,
          has_subscription_token: false,
          tos_accepted_at: null,
          oauth_token_url: null,
          oauth_client_id: null,
          oauth_scope: null,
          custom_header_name: null,
          preset_name: null,
          supports_model_pull: false,
          supports_model_delete: false,
          supports_model_config: false,
        }),
      })
      return <Story />
    },
  ],
} satisfies Meta<typeof AddManualModelDialog>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}
export const Closed: Story = { args: { open: false } }
