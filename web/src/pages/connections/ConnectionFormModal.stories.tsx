import type { Meta, StoryObj } from '@storybook/react-vite'
import { fn } from 'storybook/test'
import { CONNECTION_TYPE_VALUES, type ConnectionType } from '@/api/types/integrations'
import { connectionsList } from '@/mocks/handlers/integrations'
import { ConnectionFormModal } from './ConnectionFormModal'

const meta = {
  title: 'Pages/Connections/ConnectionFormModal',
  component: ConnectionFormModal,
  tags: ['autodocs'],
  parameters: {
    msw: { handlers: connectionsList },
  },
  args: {
    open: true,
    mode: 'create',
    onClose: fn(),
  },
} satisfies Meta<typeof ConnectionFormModal>

export default meta
type Story = StoryObj<typeof meta>

function makeTypeStory(type: ConnectionType): Story {
  return {
    name: `Create ${type}`,
    args: {
      open: true,
      mode: 'create',
      initialType: type,
    },
  }
}

export const TypePicker: Story = {
  args: {
    open: true,
    mode: 'create',
  },
}

// One story per connection type -- covers the full form matrix.
const typeStories = Object.fromEntries(
  CONNECTION_TYPE_VALUES.map((type) => [
    `Create_${type}`,
    makeTypeStory(type),
  ]),
) as Record<string, Story>

export const CreateGithub = typeStories['Create_github']!
export const CreateSlack = typeStories['Create_slack']!
export const CreateSmtp = typeStories['Create_smtp']!
export const CreateDatabase = typeStories['Create_database']!
export const CreateGenericHttp = typeStories['Create_generic_http']!
export const CreateOauthApp = typeStories['Create_oauth_app']!

export const EditMode: Story = {
  args: {
    open: true,
    mode: 'edit',
    connection: {
      id: 'conn-primary-github',
      name: 'primary-github',
      connection_type: 'github',
      auth_method: 'bearer_token',
      base_url: 'https://api.github.com',
      health_check_enabled: true,
      health_status: 'healthy',
      last_health_check_at: '2026-04-12T08:00:00Z',
      metadata: {},
      rate_limiter: null,
      secret_refs: [],
      webhook_receipt_retention_days: null,
      sensitive: false,
      created_at: '2026-04-01T09:00:00Z',
      updated_at: '2026-04-12T08:00:00Z',
    },
  },
}
