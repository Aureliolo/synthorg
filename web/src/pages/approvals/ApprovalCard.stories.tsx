import type { Meta, StoryObj } from '@storybook/react'
import { fn } from 'storybook/test'
import { ApprovalCard } from './ApprovalCard'
import type { ApprovalResponse } from '@/api/types/approvals'

const base: ApprovalResponse = {
  id: 'apr-1',
  action_type: 'deploy:production',
  title: 'Deploy API to production',
  description: 'Deploy latest changes to production environment',
  requested_by: 'agent-eng',
  risk_level: 'critical',
  source: 'review_gate',
  status: 'pending',
  task_id: 'task-1',
  metadata: {},
  decided_by: null,
  decision_reason: null,
  created_at: '2026-03-27T10:00:00Z',
  decided_at: null,
  expires_at: '2026-03-27T14:00:00Z',
  evidence_package: null,
  seconds_remaining: 7200,
  urgency_level: 'high',
}

const actions = {
  onSelect: fn(),
  onApprove: fn(),
  onReject: fn(),
  onToggleSelect: fn(),
}

const meta: Meta<typeof ApprovalCard> = {
  title: 'Pages/Approvals/ApprovalCard',
  component: ApprovalCard,
  parameters: {
    a11y: { test: 'error' },
  },
  args: { ...actions, selected: false },
  decorators: [(Story) => <div className="max-w-md"><Story /></div>],
}
export default meta

type Story = StoryObj<typeof ApprovalCard>

export const CriticalPending: Story = {
  args: { approval: base },
}

export const HighPending: Story = {
  args: { approval: { ...base, risk_level: 'high', urgency_level: 'high', seconds_remaining: 10800 } },
}

export const MediumPending: Story = {
  args: { approval: { ...base, risk_level: 'medium', urgency_level: 'normal', seconds_remaining: 28800 } },
}

export const LowPending: Story = {
  args: { approval: { ...base, risk_level: 'low', urgency_level: 'no_expiry', seconds_remaining: null, expires_at: null } },
}

export const Selected: Story = {
  args: { approval: base, selected: true },
}

export const Approved: Story = {
  args: {
    approval: { ...base, status: 'approved', decided_by: 'admin', decided_at: '2026-03-27T11:00:00Z' },
  },
}

export const Rejected: Story = {
  args: {
    approval: { ...base, status: 'rejected', decision_reason: 'Too risky', decided_at: '2026-03-27T11:00:00Z' },
  },
}

export const Expired: Story = {
  args: {
    approval: { ...base, status: 'expired', seconds_remaining: 0 },
  },
}

export const NoExpiry: Story = {
  args: {
    approval: { ...base, expires_at: null, seconds_remaining: null, urgency_level: 'no_expiry' },
  },
}
