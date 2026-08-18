import type { Meta, StoryObj } from '@storybook/react'
import { MeetingAgendaSection } from './MeetingAgendaSection'
import type { MeetingAgenda } from '@/api/types/meetings'

const meta = {
  title: 'Meetings/MeetingAgendaSection',
  component: MeetingAgendaSection,
  tags: ['autodocs'],
  args: {
    participantNames: {
      'agent-alice': 'Alice Smith',
      'agent-bob': 'Bob Jones',
      'agent-carol': 'Carol Diaz',
    },
  },
} satisfies Meta<typeof MeetingAgendaSection>

export default meta
type Story = StoryObj<typeof meta>

const fullAgenda: MeetingAgenda = {
  title: 'Sprint 12 Planning',
  context: 'Bi-weekly sprint planning session for the engineering team.',
  items: [
    { title: 'Sprint retrospective', description: 'Review completed and carried-over items', presenter_id: 'agent-alice' },
    { title: 'Backlog grooming', description: 'Prioritize items for next sprint', presenter_id: 'agent-bob' },
    { title: 'Capacity planning', description: 'Review team availability', presenter_id: null },
  ],
}

export const Default: Story = {
  args: { agenda: fullAgenda },
}

export const MinimalAgenda: Story = {
  args: {
    agenda: {
      title: 'Daily Standup',
      context: 'Regular sync',
      items: [{ title: 'Status updates', description: '', presenter_id: null }],
    },
  },
}

export const EmptyItems: Story = {
  args: {
    agenda: {
      title: 'Ad-hoc Discussion',
      context: 'Triggered by deployment event',
      items: [],
    },
  },
}
