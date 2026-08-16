import type { Meta, StoryObj } from '@storybook/react'
import { CareerTimeline } from './CareerTimeline'
import type { CareerEvent } from '@/api/types/agents'

const FIXED_BASE = new Date('2026-03-26T12:00:00.000Z')

function makeEvent(
  type: CareerEvent['event_type'],
  daysAgo: number,
  desc: string,
  by: string = 'System',
): CareerEvent {
  return {
    event_type: type,
    timestamp: new Date(FIXED_BASE.getTime() - daysAgo * 86_400_000).toISOString(),
    description: desc,
    initiated_by: by,
    metadata: {},
  }
}

const events: CareerEvent[] = [
  makeEvent('hired', 90, 'Joined as Junior Engineer', 'HR System'),
  makeEvent('onboarded', 85, 'Completed onboarding program', 'System'),
  makeEvent('promoted', 30, 'Promoted to Mid-level Engineer', 'CTO'),
]

const meta = {
  title: 'Agents/CareerTimeline',
  component: CareerTimeline,
  decorators: [(Story) => <div className="p-6 max-w-lg"><Story /></div>],
} satisfies Meta<typeof CareerTimeline>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = { args: { events } }

export const Empty: Story = { args: { events: [] } }

export const LongCareer: Story = {
  args: {
    events: [
      makeEvent('hired', 365, 'Joined the organisation', 'HR System'),
      makeEvent('onboarded', 360, 'Completed onboarding', 'System'),
      makeEvent('promoted', 270, 'Promoted to Mid-level', 'CTO'),
      makeEvent('promoted', 120, 'Promoted to Senior', 'CTO'),
      makeEvent('demoted', 60, 'Demoted to Mid-level after restructuring', 'VP Engineering'),
      makeEvent('promoted', 10, 'Promoted back to Senior', 'CTO'),
    ],
  },
}

export const Fired: Story = {
  args: {
    events: [
      makeEvent('hired', 90, 'Joined the team', 'HR System'),
      makeEvent('onboarded', 85, 'Completed onboarding', 'System'),
      makeEvent('fired', 1, 'Contract terminated', 'HR System'),
    ],
  },
}
