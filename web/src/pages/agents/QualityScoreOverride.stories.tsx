import type { Meta, StoryObj } from '@storybook/react'
import { http, HttpResponse } from 'msw'
import { QualityScoreOverride } from './QualityScoreOverride'

const API_BASE = '/api/v1/agents/agent-001/quality'

const activeOverride = {
  agent_id: 'agent-001',
  score: 8.5,
  reason: 'Consistently high-quality code reviews with thorough feedback',
  applied_by: 'manager-alice',
  applied_at: '2026-03-15T12:00:00Z',
  expires_at: null,
}

const meta = {
  title: 'Agents/QualityScoreOverride',
  component: QualityScoreOverride,
  parameters: {
    a11y: { test: 'error' },
  },
  decorators: [(Story) => <div className="max-w-lg p-6"><Story /></div>],
  args: { agentId: 'agent-001' },
} satisfies Meta<typeof QualityScoreOverride>

export default meta
type Story = StoryObj<typeof meta>

export const NoOverride: Story = {
  beforeEach({ msw }) {
    msw.use(http.get(API_BASE + '/override', () =>
      HttpResponse.json({ success: false, error: 'Not found' }, { status: 404 }),
    ))
  }
}

export const ActiveOverride: Story = {
  beforeEach({ msw }) {
    msw.use(http.get(API_BASE + '/override', () =>
      HttpResponse.json({ success: true, data: activeOverride }),
    ))
  }
}

export const WithExpiration: Story = {
  beforeEach({ msw }) {
    msw.use(http.get(API_BASE + '/override', () =>
      HttpResponse.json({
        success: true,
        data: { ...activeOverride, expires_at: '2026-03-22T12:00:00Z' },
      }),
    ))
  }
}

export const Error: Story = {
  beforeEach({ msw }) {
    msw.use(http.get(API_BASE + '/override', () =>
      HttpResponse.json(
        { success: false, error: 'Internal server error' },
        { status: 500 },
      ),
    ))
  }
}
