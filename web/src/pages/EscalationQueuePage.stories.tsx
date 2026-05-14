import type { Meta, StoryObj } from '@storybook/react-vite'
import { MemoryRouter } from 'react-router'
import EscalationQueuePage from './EscalationQueuePage'
import { useEscalationsStore } from '@/stores/escalations'
import type { EscalationResponse } from '@/api/types/escalations'

const sampleEscalations: readonly EscalationResponse[] = [
  {
    conflict_id: 'conf-1',
    status: 'pending',
    escalation: {
      id: 'esc-1',
      status: 'pending',
      created_at: '2026-04-28T07:30:00+00:00',
      expires_at: '2026-04-28T11:30:00+00:00',
      decided_at: null,
      decided_by: null,
      decision: null,
      conflict: {
        id: 'conf-1',
        type: 'other',
        task_id: 'task-1',
        subject: 'Decision authority on credentials rollout',
        detected_at: '2026-04-28T07:25:00+00:00',
        is_cross_department: true,
        positions: [
          {
            agent_id: 'agent-cto',
            agent_department: 'engineering',
            agent_level: 'c_suite',
            position: 'Roll out today; risk is acceptable.',
            reasoning: 'Mitigations are in place.',
            timestamp: '2026-04-28T07:26:00+00:00',
          },
          {
            agent_id: 'agent-cso',
            agent_department: 'security',
            agent_level: 'c_suite',
            position: 'Hold for 24h pending pen-test results.',
            reasoning: 'Pen-test surfaced ambiguous findings.',
            timestamp: '2026-04-28T07:27:00+00:00',
          },
        ],
      },
    },
  },
]

const meta = {
  title: 'Pages/EscalationQueuePage',
  component: EscalationQueuePage,
  parameters: { layout: 'fullscreen' },
  decorators: [
    (Story) => {
      useEscalationsStore.setState({
        escalations: sampleEscalations,
        loading: false,
        error: null,
      })
      return (
        <MemoryRouter>
          <Story />
        </MemoryRouter>
      )
    },
  ],
} satisfies Meta<typeof EscalationQueuePage>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const Loading: Story = {
  decorators: [
    (Story) => {
      useEscalationsStore.setState({ escalations: [], loading: true, error: null })
      return (
        <MemoryRouter>
          <Story />
        </MemoryRouter>
      )
    },
  ],
}

export const Empty: Story = {
  decorators: [
    (Story) => {
      useEscalationsStore.setState({ escalations: [], loading: false, error: null })
      return (
        <MemoryRouter>
          <Story />
        </MemoryRouter>
      )
    },
  ],
}

export const ErrorState: Story = {
  decorators: [
    (Story) => {
      useEscalationsStore.setState({
        escalations: [],
        loading: false,
        error: 'Backend unreachable',
      })
      return (
        <MemoryRouter>
          <Story />
        </MemoryRouter>
      )
    },
  ],
}
