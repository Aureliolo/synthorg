import type { Meta, StoryObj } from '@storybook/react'

import type { ClientRequest, RequestStatus } from '@/api/endpoints/clients'

import { RequestCard } from './request-card'

function buildRequest(
  status: RequestStatus,
  overrides: Partial<ClientRequest> = {},
): ClientRequest {
  return {
    request_id: '11111111-2222-3333-4444-555555555555',
    client_id: 'acme-corp',
    requirement: {
      title: 'Add CSV export to the dashboard',
      description:
        'Operators need a CSV download for the past-30-days task table.',
      task_type: 'feature',
      priority: 'medium',
      estimated_complexity: 'medium',
    },
    status,
    created_at: '2026-04-29T12:00:00Z',
    metadata: {},
    ...overrides,
  }
}

const meta = {
  title: 'UI/RequestCard',
  component: RequestCard,
  tags: ['autodocs'],
  args: {
    onScope: () => {},
    onApprove: () => {},
    onReject: () => {},
  },
  argTypes: {
    onScope: { action: 'onScope' },
    onApprove: { action: 'onApprove' },
    onReject: { action: 'onReject' },
  },
} satisfies Meta<typeof RequestCard>

export default meta
type Story = StoryObj<typeof meta>

export const Submitted: Story = {
  args: {
    request: buildRequest('submitted'),
    pending: {},
  },
}

export const Triaging: Story = {
  args: {
    request: buildRequest('triaging'),
    pending: {},
  },
}

export const Scoping: Story = {
  args: {
    request: buildRequest('scoping'),
    pending: {},
  },
}

export const Approved: Story = {
  args: {
    request: buildRequest('approved'),
    pending: {},
  },
}

export const TaskCreated: Story = {
  args: {
    request: buildRequest('task_created'),
    pending: {},
  },
}

export const Cancelled: Story = {
  args: {
    request: buildRequest('cancelled'),
    pending: {},
  },
}

export const PendingFlightDisablesActions: Story = {
  args: {
    request: buildRequest('triaging'),
    pending: { '11111111-2222-3333-4444-555555555555': true },
  },
}
