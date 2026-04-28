import type { Meta, StoryObj } from '@storybook/react-vite'
import { AuditLogDrawer } from './AuditLogDrawer'
import { useProvidersStore } from '@/stores/providers'
import type { ProviderAuditEvent } from '@/api/types/providers'

const SAMPLE_EVENTS: readonly ProviderAuditEvent[] = [
  {
    id: 4,
    provider_name: 'test-provider',
    event_type: 'provider_credentials_rotated',
    actor: { id: 'user-42', label: 'Operator (CEO)' },
    payload: { auth_type: 'api_key', masked_secret: 'sk_l***f93a' },
    occurred_at: '2026-04-28T08:42:00+00:00',
  },
  {
    id: 3,
    provider_name: 'test-provider',
    event_type: 'models_synced',
    actor: { id: 'user-42', label: 'Operator (CEO)' },
    payload: {
      added_count: 2,
      removed_count: 0,
      updated_count: 5,
      replace_existing: true,
    },
    occurred_at: '2026-04-28T08:30:00+00:00',
  },
  {
    id: 2,
    provider_name: 'test-provider',
    event_type: 'model_added',
    actor: { id: 'user-42', label: 'Operator (CEO)' },
    payload: { model_id: 'example-large-001', alias: 'large' },
    occurred_at: '2026-04-28T08:15:00+00:00',
  },
  {
    id: 1,
    provider_name: 'test-provider',
    event_type: 'provider_created',
    actor: { id: 'system', label: 'provider-management' },
    payload: { driver: 'litellm', auth_type: 'api_key', model_count: 0 },
    occurred_at: '2026-04-28T08:00:00+00:00',
  },
]

const meta = {
  title: 'Providers/AuditLogDrawer',
  component: AuditLogDrawer,
  args: {
    providerName: 'test-provider',
    open: true,
    onClose: () => {},
  },
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        auditEvents: SAMPLE_EVENTS,
        auditNextCursor: null,
        auditHasMore: false,
        auditLoading: false,
        auditLoadingMore: false,
        auditError: null,
        auditProviderName: 'test-provider',
        fetchAudit: async () => {},
        fetchMoreAudit: async () => {},
        clearAudit: () => {},
      })
      return <Story />
    },
  ],
} satisfies Meta<typeof AuditLogDrawer>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const WithMore: Story = {
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        auditHasMore: true,
        auditNextCursor: 'cursor-token',
      })
      return <Story />
    },
  ],
}

export const Loading: Story = {
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        auditEvents: [],
        auditLoading: true,
      })
      return <Story />
    },
  ],
}

export const Empty: Story = {
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        auditEvents: [],
        auditLoading: false,
        auditHasMore: false,
      })
      return <Story />
    },
  ],
}

export const ErrorState: Story = {
  decorators: [
    (Story) => {
      useProvidersStore.setState({
        auditEvents: [],
        auditLoading: false,
        auditError: 'Failed to load audit log',
      })
      return <Story />
    },
  ],
}
