import type { Meta, StoryObj } from '@storybook/react'
import { TaskStatusIndicator, PriorityBadge } from './task-status-indicator'
import type { Priority, TaskStatus } from '@/api/types/enums'

// ── TaskStatusIndicator ─────────────────────────────────────

const statusMeta = {
  title: 'UI/TaskStatusIndicator',
  component: TaskStatusIndicator,
  tags: ['autodocs'],
} satisfies Meta<typeof TaskStatusIndicator>

export default statusMeta
type StatusStory = StoryObj<typeof statusMeta>

const ALL_STATUSES: TaskStatus[] = [
  'created', 'assigned', 'in_progress', 'in_review', 'completed',
  'blocked', 'failed', 'interrupted', 'suspended', 'cancelled',
]

export const Default: StatusStory = {
  args: { status: 'in_progress' },
}

export const WithLabel: StatusStory = {
  args: { status: 'in_progress', label: true },
}

export const Pulsing: StatusStory = {
  args: { status: 'in_progress', label: true, pulse: true },
}

// `announce` toggles a live region (aria-live="polite") so screen
// readers receive the status change when a task transitions in real
// time over WebSocket.  The visible output is identical to
// WithLabel; this story exists so the live-region capability is
// discoverable in Storybook navigation alongside the other variants.
export const Announce: StatusStory = {
  args: { status: 'in_progress', label: true, announce: true },
}

export const AllStatuses: StatusStory = {
  args: { status: 'created', label: true },
  render: () => (
    <div className="flex flex-col gap-2">
      {ALL_STATUSES.map((status) => (
        <TaskStatusIndicator key={status} status={status} label />
      ))}
    </div>
  ),
}

// ── PriorityBadge ───────────────────────────────────────────
// PriorityBadges uses a custom render that ignores args -- it showcases all 4 priority levels.

export const PriorityBadges: StatusStory = {
  args: { status: 'created' },
  render: () => (
    <div className="flex gap-2">
      {(['critical', 'high', 'medium', 'low'] as Priority[]).map((p) => (
        <PriorityBadge key={p} priority={p} />
      ))}
    </div>
  ),
}
