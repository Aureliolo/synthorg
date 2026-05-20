import type { Meta, StoryObj } from '@storybook/react-vite'
import { fn } from 'storybook/test'
import type { Connection, HealthReport } from '@/api/types/integrations'
import { ConnectionCard } from './ConnectionCard'

const baseConnection: Connection = {
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
}

const healthyReport: HealthReport = {
  connection_name: 'primary-github',
  status: 'healthy',
  latency_ms: 42,
  error_detail: null,
  checked_at: '2026-04-12T08:00:00Z',
  consecutive_failures: 0,
}

const meta = {
  title: 'Pages/Connections/ConnectionCard',
  component: ConnectionCard,
  tags: ['autodocs'],
  args: {
    onRunHealthCheck: fn(),
    onEdit: fn(),
    onDelete: fn(),
  },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof ConnectionCard>

export default meta
type Story = StoryObj<typeof meta>

export const Healthy: Story = {
  args: {
    connection: baseConnection,
    report: healthyReport,
    checking: false,
  },
}

export const Degraded: Story = {
  args: {
    connection: { ...baseConnection, health_status: 'degraded' },
    report: { ...healthyReport, status: 'degraded', latency_ms: 1200 },
    checking: false,
  },
}

export const Unhealthy: Story = {
  args: {
    connection: { ...baseConnection, health_status: 'unhealthy' },
    report: {
      ...healthyReport,
      status: 'unhealthy',
      latency_ms: null,
      error_detail: 'Connection refused after 3 retries',
      consecutive_failures: 4,
    },
    checking: false,
  },
}

export const Unknown: Story = {
  args: {
    connection: {
      ...baseConnection,
      health_status: 'unknown',
      last_health_check_at: null,
    },
    report: null,
    checking: false,
  },
}

export const Checking: Story = {
  args: {
    connection: baseConnection,
    report: healthyReport,
    checking: true,
  },
}
