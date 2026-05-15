import type { Meta, StoryObj } from '@storybook/react-vite'
import { fn } from 'storybook/test'
import type { Connection } from '@/api/types/integrations'
import { connectionsList } from '@/mocks/handlers/integrations'
import { OauthAppCard } from './OauthAppCard'

const baseConnection: Connection = {
  id: 'conn-gh-oauth-app',
  name: 'gh-oauth-app',
  connection_type: 'oauth_app',
  auth_method: 'oauth2',
  base_url: null,
  health_check_enabled: false,
  health_status: 'unknown',
  last_health_check_at: null,
  metadata: {},
  rate_limiter: null,
  secret_refs: [],
  webhook_receipt_retention_days: null,
  created_at: '2026-04-01T09:00:00Z',
  updated_at: '2026-04-12T08:00:00Z',
}

const meta = {
  title: 'Pages/OAuthApps/OauthAppCard',
  component: OauthAppCard,
  tags: ['autodocs'],
  parameters: {
    msw: { handlers: connectionsList },
  },
  args: {
    connection: baseConnection,
    onEdit: fn(),
    onDelete: fn(),
    onConnect: fn(),
  },
  decorators: [
    (Story) => (
      <div className="max-w-md">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof OauthAppCard>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}
export const WithoutConnect: Story = {
  args: {
    onConnect: undefined,
  },
}
