import type { Meta, StoryObj } from '@storybook/react-vite'
import { ModelStalenessBadge } from './model-staleness-badge'

const meta = {
  title: 'UI/ModelStalenessBadge',
  component: ModelStalenessBadge,
  parameters: { layout: 'centered' },
} satisfies Meta<typeof ModelStalenessBadge>

export default meta
type Story = StoryObj<typeof meta>

export const RemovedWithSuccessor: Story = {
  args: {
    stale: {
      reason: 'removed_from_catalog',
      flagged_at: '2026-06-01T12:00:00+00:00',
      last_seen: '2026-05-30',
      successor_model_id: 'example-large-002',
    },
  },
}

export const Deprecated: Story = {
  args: {
    stale: {
      reason: 'deprecated',
      flagged_at: '2026-06-01T12:00:00+00:00',
      last_seen: null,
      successor_model_id: null,
    },
  },
}

export const NotStale: Story = { args: { stale: null } }
