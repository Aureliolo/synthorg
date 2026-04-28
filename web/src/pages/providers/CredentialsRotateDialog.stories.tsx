import type { Meta, StoryObj } from '@storybook/react-vite'
import { CredentialsRotateDialog } from './CredentialsRotateDialog'
import { useProvidersStore } from '@/stores/providers'
import type { ProviderConfig } from '@/api/types/providers'

const apiKeyProvider: ProviderConfig = {
  driver: 'litellm',
  litellm_provider: 'cloud-test',
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
  preset_name: 'cloud-test',
  supports_model_pull: false,
  supports_model_delete: false,
  supports_model_config: false,
}

const subscriptionProvider: ProviderConfig = {
  ...apiKeyProvider,
  auth_type: 'subscription',
  has_api_key: false,
  has_subscription_token: true,
}

const customHeaderProvider: ProviderConfig = {
  ...apiKeyProvider,
  auth_type: 'custom_header',
  has_api_key: false,
  has_custom_header: true,
  custom_header_name: 'X-Custom-Auth',
}

const oauthProvider: ProviderConfig = {
  ...apiKeyProvider,
  auth_type: 'oauth',
  has_api_key: false,
  has_oauth_credentials: true,
  oauth_token_url: 'https://oauth.example.com/token',
  oauth_client_id: 'client-1234',
  oauth_scope: 'read:models',
}

const meta = {
  title: 'Providers/CredentialsRotateDialog',
  component: CredentialsRotateDialog,
  args: {
    providerName: 'test-provider',
    open: true,
    onClose: () => {},
  },
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        rotateCredentials: async () => apiKeyProvider,
      })
      return <Story />
    },
  ],
} satisfies Meta<typeof CredentialsRotateDialog>

export default meta
type Story = StoryObj<typeof meta>

export const ApiKey: Story = { args: { provider: apiKeyProvider } }
export const Subscription: Story = { args: { provider: subscriptionProvider } }
export const CustomHeader: Story = { args: { provider: customHeaderProvider } }
export const OAuth: Story = { args: { provider: oauthProvider } }
export const Closed: Story = { args: { provider: apiKeyProvider, open: false } }
