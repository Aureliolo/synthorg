import type { Meta, StoryObj } from '@storybook/react'
import { http, HttpResponse } from 'msw'
import type { getCallAnalytics } from '@/api/endpoints/budget'
import type { AnalyticsAggregation } from '@/api/types/budget'
import { successFor } from '@/mocks/handlers/helpers'
import { CallAnalyticsSection } from './CallAnalyticsSection'

const POPULATED: AnalyticsAggregation = {
  total_calls: 293,
  success_count: 280,
  failure_count: 3,
  unreported_count: 10,
  success_rate: 0.989,
  retry_count: 14,
  retry_rate: 0.048,
  input_tokens: 2_000_000,
  cached_input_tokens: 1_240_000,
  cached_input_share: 0.62,
  avg_latency_ms: 840,
  p95_latency_ms: 2_600,
  by_finish_reason: [
    ['stop', 260],
    ['tool_calls', 30],
    ['length', 3],
  ],
  orchestration_ratio: {
    alert_level: 'normal',
    coordination_tokens: 210_000,
    productive_tokens: 1_700_000,
    ratio: 0.11,
    system_tokens: 90_000,
    total_tokens: 2_000_000,
  },
}

// A ledger whose calls reported no cache figures and no outcome: the share
// and the success rate are absences, not zeros.
const UNREPORTED: AnalyticsAggregation = {
  ...POPULATED,
  success_count: 0,
  failure_count: 0,
  unreported_count: 293,
  success_rate: null,
  input_tokens: 0,
  cached_input_tokens: 0,
  cached_input_share: null,
  avg_latency_ms: null,
  p95_latency_ms: null,
}

function analyticsHandler(payload: AnalyticsAggregation) {
  return [
    http.get('/api/v1/budget/call-analytics', () =>
      HttpResponse.json(successFor<typeof getCallAnalytics>(payload)),
    ),
  ]
}

const meta = {
  title: 'Pages/Budget/CallAnalyticsSection',
  component: CallAnalyticsSection,
  tags: ['autodocs'],
} satisfies Meta<typeof CallAnalyticsSection>

export default meta
type Story = StoryObj<typeof meta>

export const Populated: Story = {
  beforeEach({ msw }) {
    msw.use(...analyticsHandler(POPULATED))
  },
}

export const Unreported: Story = {
  beforeEach({ msw }) {
    msw.use(...analyticsHandler(UNREPORTED))
  },
}

export const Empty: Story = {
  beforeEach({ msw }) {
    msw.use(...analyticsHandler({ ...POPULATED, total_calls: 0 }))
  },
}

export const Failed: Story = {
  beforeEach({ msw }) {
    msw.use(
      http.get('/api/v1/budget/call-analytics', () => new HttpResponse(null, { status: 500 })),
    )
  },
}
