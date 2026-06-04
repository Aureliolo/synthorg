import type { Meta, StoryObj } from '@storybook/react-vite'
import { EscalationDetailDrawer } from './EscalationDetailDrawer'
import { useEscalationsStore } from '@/stores/escalations'
import type { EscalationResponse } from '@/api/types/escalations'

const sampleDetail: EscalationResponse = {
  conflict_id: 'conflict-1',
  status: 'pending',
  escalation: {
    id: 'esc-1',
    status: 'pending',
    created_at: '2026-04-28T08:00:00+00:00',
    expires_at: '2026-04-29T08:00:00+00:00',
    decided_at: null,
    decided_by: null,
    decision: null,
    conflict: {
      id: 'conflict-1',
      type: 'other',
      task_id: 'task-1',
      subject: 'Decision authority on credentials rollout',
      detected_at: '2026-04-28T07:55:00+00:00',
      is_cross_department: true,
      positions: [
        {
          agent_id: 'agent-cto',
          agent_department: 'engineering',
          agent_level: 'c_suite',
          position: 'Roll out today; risk is acceptable.',
          reasoning: 'Mitigations are in place.',
          timestamp: '2026-04-28T07:56:00+00:00',
        },
        {
          agent_id: 'agent-cso',
          agent_department: 'security',
          agent_level: 'c_suite',
          position: 'Hold for 24h pending pen-test results.',
          reasoning: 'Pen-test surfaced ambiguous findings.',
          timestamp: '2026-04-28T07:57:00+00:00',
        },
      ],
    },
  },
}

const meta = {
  title: 'Escalations/EscalationDetailDrawer',
  component: EscalationDetailDrawer,
  args: {
    escalationId: 'esc-1',
    open: true,
    onClose: () => {},
  },
  decorators: [
    (Story) => {
      useEscalationsStore.setState({
        selected: sampleDetail,
        detailLoading: false,
        detailError: null,
        submitting: false,
        fetchEscalationDetail: () => Promise.resolve(),
        clearDetail: () => {},
        submitDecision: () => Promise.resolve(sampleDetail),
        cancelEscalation: () => Promise.resolve(sampleDetail),
      })
      return <Story />
    },
  ],
} satisfies Meta<typeof EscalationDetailDrawer>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const Loading: Story = {
  decorators: [
    (Story) => {
      useEscalationsStore.setState({
        selected: null,
        detailLoading: true,
      })
      return <Story />
    },
  ],
}

export const ErrorState: Story = {
  decorators: [
    (Story) => {
      useEscalationsStore.setState({
        selected: null,
        detailLoading: false,
        detailError: 'Could not load escalation',
      })
      return <Story />
    },
  ],
}
