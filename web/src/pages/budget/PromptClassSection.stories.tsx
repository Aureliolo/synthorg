import type { Meta, StoryObj } from '@storybook/react'
import { http, HttpResponse } from 'msw'
import type { getPromptClassBreakdown } from '@/api/endpoints/budget'
import type { PromptClassBreakdown } from '@/api/types/budget'
import { successFor } from '@/mocks/handlers/helpers'
import { PromptClassSection } from './PromptClassSection'

const POPULATED: PromptClassBreakdown = {
  rows: [
    {
      prompt_class_id: 'system:cos:chat',
      capability: 'capable',
      total_cost: 1.42,
      currency: 'USD',
      call_count: 128,
      input_tokens: 90_000,
      output_tokens: 22_000,
      avg_latency_ms: 540,
      p95_latency_ms: 1180,
      cache_hit_rate: 0.31,
      retry_rate: 0.04,
      success_rate: 0.97,
    },
    {
      prompt_class_id: 'system:memory:rerank',
      capability: 'basic',
      total_cost: 0.18,
      currency: 'USD',
      call_count: 512,
      input_tokens: 40_000,
      output_tokens: 6_000,
      avg_latency_ms: 120,
      p95_latency_ms: 260,
      cache_hit_rate: 0.62,
      retry_rate: 0.0,
      success_rate: 1.0,
    },
    {
      prompt_class_id: 'system:research:synthesis',
      capability: 'expert',
      total_cost: 3.05,
      currency: 'USD',
      call_count: 41,
      input_tokens: 210_000,
      output_tokens: 88_000,
      avg_latency_ms: 2200,
      p95_latency_ms: 4800,
      cache_hit_rate: null,
      retry_rate: 0.12,
      success_rate: 0.9,
    },
  ],
}

function breakdownHandler(payload: PromptClassBreakdown) {
  return [
    http.get('/api/v1/budget/prompt-class-breakdown', () =>
      HttpResponse.json(successFor<typeof getPromptClassBreakdown>(payload)),
    ),
  ]
}

const meta = {
  title: 'Pages/Budget/PromptClassSection',
  component: PromptClassSection,
  tags: ['autodocs'],
} satisfies Meta<typeof PromptClassSection>

export default meta
type Story = StoryObj<typeof meta>

export const Populated: Story = {
  beforeEach({ msw }) {
    msw.use(...breakdownHandler(POPULATED))
  },
}

export const Empty: Story = {
  beforeEach({ msw }) {
    msw.use(...breakdownHandler({ rows: [] }))
  },
}
