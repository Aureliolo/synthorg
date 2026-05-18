import type { Meta, StoryObj } from '@storybook/react'
import { ApprovalTimeline } from './ApprovalTimeline'
import type { ApprovalResponse } from '@/api/types/approvals'

const base: ApprovalResponse = {
  id: 'apr-1',
  action_type: 'deploy:production',
  title: 'Deploy API v2',
  description: 'Deploy to production environment',
  requested_by: 'agent-eng',
  risk_level: 'critical',
  source: 'review_gate',
  status: 'pending',
  task_id: null,
  metadata: {},
  decided_by: null,
  decision_reason: null,
  created_at: '2026-03-27T10:00:00Z',
  decided_at: null,
  expires_at: '2026-03-27T14:00:00Z',
  evidence_package: null,
  seconds_remaining: 14400,
  urgency_level: 'high',
}

const meta: Meta<typeof ApprovalTimeline> = {
  title: 'Pages/Approvals/ApprovalTimeline',
  component: ApprovalTimeline,
  parameters: {
    a11y: { test: 'error' },
  },
}
export default meta

type Story = StoryObj<typeof ApprovalTimeline>

export const Pending: Story = {
  args: { approval: base },
}

export const Approved: Story = {
  args: {
    approval: {
      ...base,
      status: 'approved',
      decided_by: 'admin',
      decided_at: '2026-03-27T11:30:00Z',
    },
  },
}

export const Rejected: Story = {
  args: {
    approval: {
      ...base,
      status: 'rejected',
      decided_by: 'admin',
      decision_reason: 'Too risky for Friday deploy',
      decided_at: '2026-03-27T11:30:00Z',
    },
  },
}

export const Expired: Story = {
  args: {
    approval: {
      ...base,
      status: 'expired',
      seconds_remaining: 0,
      urgency_level: 'critical',
    },
  },
}
