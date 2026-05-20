/**
 * Meta improvement API endpoints.
 *
 * Provides access to improvement proposals, signal domains,
 * A/B tests, configuration, and Chief of Staff chat.
 */

import type {
  ConversationalProposeRequest,
  ProposeResult,
  ProposedApprovalSummary as GeneratedProposedApprovalSummary,
} from '../types'
import type { ApiResponse } from '../types/http'
import { apiClient, unwrap } from '../client'

// Re-export the generated DTOs so call sites that previously imported
// the hand-maintained interfaces keep working without changing every
// site at once; the source of truth is the generated openapi.gen.ts.
export type ConversationalProposeResponse = ProposeResult
export type ProposedApprovalSummary = GeneratedProposedApprovalSummary

// -- Types -------------------------------------------------------------------

export interface ProposalSummary {
  id: string
  title: string
  action_type: string
  status: string
  risk_level: string
  requested_by: string
  created_at: string
}

export interface SignalDomain {
  name: string
  status: string
}

export interface SignalsResponse {
  enabled: boolean
  domains: SignalDomain[]
}

export interface ABTestGroupMetrics {
  group: 'control' | 'treatment'
  agent_count: number
  observation_count: number
  avg_quality_score: number
  avg_success_rate: number
  total_spend: number
}

export interface ABTestSummary {
  proposal_id: string
  proposal_title: string
  control_metrics: ABTestGroupMetrics
  treatment_metrics: ABTestGroupMetrics
  verdict:
    | 'treatment_wins'
    | 'control_wins'
    | 'inconclusive'
    | 'treatment_regressed'
    | null
  observation_hours_elapsed: number
  observation_hours_total: number
}

export interface MetaConfig {
  enabled: boolean
  chief_of_staff_enabled: boolean
  config_tuning_enabled: boolean
  architecture_proposals_enabled: boolean
  prompt_tuning_enabled: boolean
  code_modification_enabled: boolean
}

export interface ChatResponse {
  answer: string
  sources: string[]
  confidence: number
}

// -- API functions -----------------------------------------------------------

const BASE = '/meta'

export async function getMetaConfig(): Promise<MetaConfig> {
  const response =
    await apiClient.get<ApiResponse<MetaConfig>>(`${BASE}/config`)
  return unwrap(response)
}

export async function listProposals(): Promise<ProposalSummary[]> {
  const response = await apiClient.get<ApiResponse<ProposalSummary[]>>(
    `${BASE}/proposals`,
  )
  return unwrap(response)
}

export async function getSignals(): Promise<SignalsResponse> {
  const response = await apiClient.get<ApiResponse<SignalsResponse>>(
    `${BASE}/signals`,
  )
  return unwrap(response)
}

export async function listABTests(): Promise<ABTestSummary[]> {
  const response = await apiClient.get<ApiResponse<ABTestSummary[]>>(
    `${BASE}/ab-tests`,
  )
  return unwrap(response)
}

export async function postChatPropose(
  message: string,
  conversationId?: string,
  project?: string,
): Promise<ConversationalProposeResponse> {
  const trimmed = message.trim()
  if (!trimmed) {
    throw new Error('Message must not be blank')
  }
  // The /meta/chat/propose endpoint is rate-limited via
  // ``per_op_rate_limit_from_policy("meta.chat.propose", key="user")``
  // (5 req / 60 s / user). Attach an Idempotency-Key so the axios 429
  // interceptor retries after Retry-After; server replays of the same
  // key are no-ops, so a retry never duplicates the parked proposal.
  const body: ConversationalProposeRequest = {
    message: trimmed,
    conversation_id: conversationId ?? null,
    project: project ?? null,
  }
  const response = await apiClient.post<
    ApiResponse<ConversationalProposeResponse>
  >(`${BASE}/chat/propose`, body, {
    headers: {
      'Idempotency-Key': crypto.randomUUID(),
    },
  })
  return unwrap(response)
}

export async function postChat(question: string): Promise<ChatResponse> {
  const trimmed = question.trim()
  if (!trimmed) {
    throw new Error('Question must not be blank')
  }
  // The /meta/chat endpoint is guarded by
  // ``per_op_rate_limit_from_policy("meta.chat", key="user")``
  // (5 req / 60 s / user).  Attach an ``Idempotency-Key`` so the
  // axios 429 interceptor retries after ``Retry-After`` instead of
  // surfacing a hard failure on ratelimit bursts -- the server treats
  // replays of the same key as a no-op, so the retry is safe.
  const response = await apiClient.post<ApiResponse<ChatResponse>>(
    `${BASE}/chat`,
    { question: trimmed },
    {
      headers: {
        'Idempotency-Key': crypto.randomUUID(),
      },
    },
  )
  return unwrap(response)
}
