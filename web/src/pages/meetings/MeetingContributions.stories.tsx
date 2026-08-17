import type { Meta, StoryObj } from '@storybook/react'
import { MeetingContributions } from './MeetingContributions'
import type { MeetingContribution } from '@/api/types/meetings'

const meta = {
  title: 'Meetings/MeetingContributions',
  component: MeetingContributions,
  tags: ['autodocs'],
  args: {
    participantNames: {
      'agent-alice': 'Alice Smith',
      'agent-bob': 'Bob Jones',
      'agent-carol': 'Carol Diaz',
    },
  },
} satisfies Meta<typeof MeetingContributions>

export default meta
type Story = StoryObj<typeof meta>

const roundRobinContributions: MeetingContribution[] = [
  {
    agent_id: 'agent-alice', content: 'Completed the API endpoint work.', phase: 'round_robin_turn',
    turn_number: 1, input_tokens: 200, output_tokens: 150, timestamp: '2026-03-25T10:01:00Z',
  },
  {
    agent_id: 'agent-bob', content: 'Working on test coverage.', phase: 'round_robin_turn',
    turn_number: 2, input_tokens: 180, output_tokens: 120, timestamp: '2026-03-25T10:02:00Z',
  },
  {
    agent_id: 'agent-alice', content: 'Summarizing: both on track, no blockers.', phase: 'summary',
    turn_number: 3, input_tokens: 100, output_tokens: 80, timestamp: '2026-03-25T10:03:00Z',
  },
]

export const RoundRobin: Story = {
  args: { contributions: roundRobinContributions },
}

const multiPhaseContributions: MeetingContribution[] = [
  {
    agent_id: 'agent-alice', content: 'Today we discuss the new billing feature.', phase: 'agenda_broadcast',
    turn_number: 1, input_tokens: 100, output_tokens: 50, timestamp: '2026-03-25T10:00:00Z',
  },
  {
    agent_id: 'agent-bob', content: 'I propose microservices.', phase: 'position_paper',
    turn_number: 1, input_tokens: 200, output_tokens: 180, timestamp: '2026-03-25T10:01:00Z',
  },
  {
    agent_id: 'agent-carol', content: 'I prefer a modular monolith.', phase: 'position_paper',
    turn_number: 2, input_tokens: 200, output_tokens: 160, timestamp: '2026-03-25T10:02:00Z',
  },
  {
    agent_id: 'agent-alice', content: 'We need to resolve the architecture disagreement.', phase: 'discussion',
    turn_number: 1, input_tokens: 150, output_tokens: 100, timestamp: '2026-03-25T10:03:00Z',
  },
  {
    agent_id: 'agent-dave', content: 'Consensus: modular monolith with decomposition plan.', phase: 'synthesis',
    turn_number: 1, input_tokens: 300, output_tokens: 250, timestamp: '2026-03-25T10:04:00Z',
  },
]

export const MultiPhase: Story = {
  args: { contributions: multiPhaseContributions },
}

export const Empty: Story = {
  args: { contributions: [] },
}
