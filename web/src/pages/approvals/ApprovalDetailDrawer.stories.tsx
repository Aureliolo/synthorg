import type { Meta, StoryObj } from '@storybook/react'
import { fn } from 'storybook/test'
import { ApprovalDetailDrawer } from './ApprovalDetailDrawer'
import type { ApprovalResponse } from '@/api/types/approvals'

const base: ApprovalResponse = {
  id: 'apr-1',
  action_type: 'deploy:production',
  title: 'Deploy authentication service v2.3',
  description: 'Deploy the latest authentication service changes to the production environment. This includes the new OAuth2 flow and session management updates.',
  requested_by: 'agent-eng-lead',
  risk_level: 'critical',
  source: 'review_gate',
  status: 'pending',
  task_id: 'task-auth-deploy',
  metadata: { environment: 'production', service: 'auth-service', version: '2.3.0' },
  decided_by: null,
  decision_reason: null,
  created_at: '2026-03-27T10:00:00Z',
  decided_at: null,
  expires_at: '2026-03-27T14:00:00Z',
  evidence_package: null,
  seconds_remaining: 7200,
  urgency_level: 'high',
}

const meta: Meta<typeof ApprovalDetailDrawer> = {
  title: 'Pages/Approvals/ApprovalDetailDrawer',
  component: ApprovalDetailDrawer,
  parameters: {
    a11y: { test: 'error' },
  },
  args: {
    open: true,
    onClose: fn(),
    onApprove: fn().mockResolvedValue(true),
    onReject: fn().mockResolvedValue(true),
  },
}
export default meta

type Story = StoryObj<typeof ApprovalDetailDrawer>

export const Pending: Story = {
  args: { approval: base },
}

export const Approved: Story = {
  args: {
    approval: {
      ...base,
      status: 'approved',
      decided_by: 'admin-user',
      decided_at: '2026-03-27T11:30:00Z',
      decision_reason: null,
    },
  },
}

export const Rejected: Story = {
  args: {
    approval: {
      ...base,
      status: 'rejected',
      decided_by: 'admin-user',
      decided_at: '2026-03-27T11:30:00Z',
      decision_reason: 'Friday deploys are not allowed per policy. Please reschedule for Monday.',
    },
  },
}

export const Loading: Story = {
  args: { approval: base, loading: true },
}

export const NoMetadata: Story = {
  args: {
    approval: { ...base, metadata: {}, task_id: null },
  },
}

export const Error: Story = {
  args: {
    open: true,
    approval: null,
    loading: false,
    error: 'Failed to load approval details',
    onClose: fn(),
    onApprove: fn().mockResolvedValue(true),
    onReject: fn().mockResolvedValue(true),
  },
}
