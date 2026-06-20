/**
 * Meta improvement API endpoints.
 *
 * Provides access to improvement proposals, signal domains,
 * A/B tests, configuration, and Chief of Staff chat.
 */

import type {
  ChatActRequest,
  ConversationalActResult,
  ConversationalProposeRequest,
  GroupChatRequest,
  GroupConverseResult,
  ProposeResult,
} from '../types'
import type {
  ApiResponse,
  PaginatedResponse,
  PaginationParams,
} from '../types/http'
import {
  apiClient,
  paginateAll,
  unwrap,
  unwrapPaginated,
  type PaginatedResult,
} from '../client'

// Re-export the generated DTO so call sites that previously imported
// the hand-maintained interface keep working without changing every
// site at once; the source of truth is the generated openapi.gen.ts.
export type ConversationalProposeResponse = ProposeResult

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

// A/B-test wire shape. The backend serialises the durable ``AbTestRecord``
// to a plain dict (not a named OpenAPI schema), so this interface is
// hand-maintained against ``_ab_test_to_dict`` in the meta controller.
export type AbTestStatus =
  | 'running'
  | 'completed'
  | 'regressed'
  | 'inconclusive'
  | 'failed'

export type ABTestVerdict =
  | 'treatment_wins'
  | 'control_wins'
  | 'inconclusive'
  | 'treatment_regressed'

export interface AbTestArm {
  name: string
  agent_count: number
  fraction: number
}

export interface AbTestRecord {
  id: string
  name: string
  status: AbTestStatus
  verdict: ABTestVerdict | null
  observation_hours_elapsed: number
  arms: readonly AbTestArm[]
  created_at: string
  updated_at: string
}

// Evolution-outcome wire shapes (durable engine evolution log).
export interface EvolutionRecentOutcome {
  agent_id: string
  axis: string
  applied: boolean
  proposed_at: string
}

export interface EvolutionSummary {
  total_proposals: number
  approval_rate: number
  most_adapted_axis: string | null
  recent_outcomes: readonly EvolutionRecentOutcome[]
}

export interface EvolutionOutcome {
  agent_id: string
  axis: string
  applied: boolean
  proposed_at: string
  recorded_at: string
}

export interface EvolutionAxisStat {
  axis: string
  count: number
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

function _pageParams(cursor: string | null): PaginationParams {
  return cursor ? { cursor } : {}
}

async function fetchProposalsPage(
  cursor: string | null,
): Promise<PaginatedResult<ProposalSummary>> {
  const response = await apiClient.get<PaginatedResponse<ProposalSummary>>(
    `${BASE}/proposals`,
    { params: _pageParams(cursor) },
  )
  return unwrapPaginated<ProposalSummary>(response)
}

export async function listProposals(): Promise<ProposalSummary[]> {
  return paginateAll<ProposalSummary>(fetchProposalsPage)
}

export async function getSignals(): Promise<SignalsResponse> {
  const response = await apiClient.get<ApiResponse<SignalsResponse>>(
    `${BASE}/signals`,
  )
  return unwrap(response)
}

async function fetchABTestsPage(
  cursor: string | null,
): Promise<PaginatedResult<AbTestRecord>> {
  const response = await apiClient.get<PaginatedResponse<AbTestRecord>>(
    `${BASE}/ab-tests`,
    { params: _pageParams(cursor) },
  )
  return unwrapPaginated<AbTestRecord>(response)
}

export async function listABTests(): Promise<AbTestRecord[]> {
  return paginateAll<AbTestRecord>(fetchABTestsPage)
}

export async function getEvolutionSummary(): Promise<EvolutionSummary> {
  const response = await apiClient.get<ApiResponse<EvolutionSummary>>(
    `${BASE}/evolution/summary`,
  )
  return unwrap(response)
}

async function fetchEvolutionOutcomesPage(
  cursor: string | null,
): Promise<PaginatedResult<EvolutionOutcome>> {
  const response = await apiClient.get<PaginatedResponse<EvolutionOutcome>>(
    `${BASE}/evolution/outcomes`,
    { params: _pageParams(cursor) },
  )
  return unwrapPaginated<EvolutionOutcome>(response)
}

export async function listEvolutionOutcomes(): Promise<EvolutionOutcome[]> {
  return paginateAll<EvolutionOutcome>(fetchEvolutionOutcomesPage)
}

export async function getEvolutionAxisStats(): Promise<EvolutionAxisStat[]> {
  const response = await apiClient.get<ApiResponse<{ axes: EvolutionAxisStat[] }>>(
    `${BASE}/evolution/axes/stats`,
  )
  return unwrap(response).axes
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

export async function postChatGroup(
  message: string,
  agentIds: readonly string[],
  conversationId?: string,
): Promise<GroupConverseResult> {
  const trimmed = message.trim()
  if (!trimmed) {
    throw new Error('Message must not be blank')
  }
  // The /meta/chat/group endpoint is rate-limited via
  // ``per_op_rate_limit_from_policy("meta.chat.group", key="user")``
  // (5 req / 60 s / user). Attach an Idempotency-Key so the axios 429
  // interceptor retries after Retry-After; a server replay of the same
  // key is a no-op, so a retry never double-runs a round.
  const body: GroupChatRequest = {
    message: trimmed,
    conversation_id: conversationId ?? null,
    // Initial roster ids (registry UUIDs from /agents/active); ignored
    // by the server when continuing an existing conversation.
    participants: agentIds,
  }
  const response = await apiClient.post<ApiResponse<GroupConverseResult>>(
    `${BASE}/chat/group`,
    body,
    {
      headers: {
        'Idempotency-Key': crypto.randomUUID(),
      },
    },
  )
  return unwrap(response)
}

export async function postChatAct(
  instruction: string,
  agent: string,
  conversationId?: string,
): Promise<ConversationalActResult> {
  const trimmedInstruction = instruction.trim()
  const trimmedAgent = agent.trim()
  if (!trimmedInstruction) {
    throw new Error('Instruction must not be blank')
  }
  if (!trimmedAgent) {
    throw new Error('Agent must not be blank')
  }
  // The /meta/chat/act endpoint is rate-limited via
  // ``per_op_rate_limit_from_policy("meta.chat.act", key="user")``
  // (5 req / 60 s / user). Attach an Idempotency-Key so the axios 429
  // interceptor retries after Retry-After; a server replay of the same
  // key is a no-op, so a retry never double-runs an action.
  const body: ChatActRequest = {
    instruction: trimmedInstruction,
    agent: trimmedAgent,
    conversation_id: conversationId ?? null,
  }
  const response = await apiClient.post<ApiResponse<ConversationalActResult>>(
    `${BASE}/chat/act`,
    body,
    {
      headers: {
        'Idempotency-Key': crypto.randomUUID(),
      },
    },
  )
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
