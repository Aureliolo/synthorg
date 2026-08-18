import type { Meta, StoryObj } from '@storybook/react'
import { ContributionBubble } from './ContributionBubble'
import type { MeetingContribution } from '@/api/types/meetings'

const meta = {
  title: 'Meetings/ContributionBubble',
  component: ContributionBubble,
  tags: ['autodocs'],
  args: {
    participantNames: {
      'agent-alice': 'Alice Smith',
      'agent-bob': 'Bob Jones',
      'agent-carol': 'Carol Diaz',
    },
  },
} satisfies Meta<typeof ContributionBubble>

export default meta
type Story = StoryObj<typeof meta>

const base: MeetingContribution = {
  agent_id: 'agent-alice',
  content: 'Completed the API endpoint work. All tests are passing and the PR is ready for review.',
  phase: 'round_robin_turn',
  turn_number: 1,
  input_tokens: 200,
  output_tokens: 150,
  timestamp: '2026-03-25T10:01:00Z',
}

export const RoundRobinTurn: Story = {
  args: { contribution: base },
}

export const PositionPaper: Story = {
  args: {
    contribution: {
      ...base,
      phase: 'position_paper',
      agent_id: 'agent-bob',
      content: 'I propose we adopt a microservices architecture for the new billing system. The current monolith is becoming a bottleneck for deployment velocity.',
    },
  },
}

export const Discussion: Story = {
  args: {
    contribution: {
      ...base,
      phase: 'discussion',
      content: 'I disagree with the microservices approach for now. We should start with a modular monolith.',
    },
  },
}

export const Synthesis: Story = {
  args: {
    contribution: {
      ...base,
      phase: 'synthesis',
      agent_id: 'agent-carol',
      content: 'After reviewing all positions, the consensus is to proceed with a modular monolith that can be decomposed later.',
    },
  },
}

export const Summary: Story = {
  args: {
    contribution: {
      ...base,
      phase: 'summary',
      content: 'Meeting concluded with 3 action items assigned. Next review in 2 weeks.',
    },
  },
}
