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

// One story per connection type -- covers the full form matrix. Keying on the
// ``Create_${ConnectionType}`` template type makes a renamed connection type a
// compile error at the require sites below rather than a silent undefined.
const typeStories = Object.fromEntries(
  CONNECTION_TYPE_VALUES.map((type) => [
    `Create_${type}`,
    makeTypeStory(type),
  ]),
) as Partial<Record<`Create_${ConnectionType}`, Story>>

function requireTypeStory(storyKey: `Create_${ConnectionType}`): Story {
  const story = typeStories[storyKey]
  if (!story) {
    throw new Error(`Missing story variant: ${storyKey}`)
  }
  return story
}

export const CreateGithub = requireTypeStory('Create_github')
export const CreateSlack = requireTypeStory('Create_slack')
export const CreateSmtp = requireTypeStory('Create_smtp')
export const CreateDatabase = requireTypeStory('Create_database')
export const CreateGenericHttp = requireTypeStory('Create_generic_http')
export const CreateOauthApp = requireTypeStory('Create_oauth_app')

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
      health: { status: 'healthy', last_check_at: '2026-04-12T08:00:00Z' },
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
