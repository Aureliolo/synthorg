import type { Meta, StoryObj } from '@storybook/react-vite'
import { ProviderHealthMetrics } from './ProviderHealthMetrics'
import type { ProviderHealthSummary } from '@/api/types/providers'

const baseHealth: ProviderHealthSummary = {
  last_check_timestamp: '2026-03-27T12:00:00Z',
  avg_response_time_ms: 250,
  error_rate_percent_24h: 1.5,
  calls_last_24h: 1234,
  health_status: 'up',
  liveness_calls: 5,
  liveness_error_rate_percent: 0,
  total_tokens_24h: 245000,
  total_cost_24h: 3.72,
}

const meta = {
  title: 'Providers/ProviderHealthMetrics',
  component: ProviderHealthMetrics,
  tags: ['autodocs'],
} satisfies Meta<typeof ProviderHealthMetrics>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = { args: { health: baseHealth } }

export const HighUsage: Story = {
  args: {
    health: {
      ...baseHealth,
      calls_last_24h: 50000,
      total_tokens_24h: 12500000,
      total_cost_24h: 187.43,
    },
  },
}

export const NoUsage: Story = {
  args: {
    health: {
      ...baseHealth,
      calls_last_24h: 0,
      total_tokens_24h: 0,
      total_cost_24h: 0,
      health_status: 'unknown',
      last_check_timestamp: null,
      avg_response_time_ms: null,
      error_rate_percent_24h: 0,
    },
  },
}
