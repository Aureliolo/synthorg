/**
 * Meta improvement API endpoints.
 *
 * Provides access to improvement proposals, signal domains,
 * A/B tests, configuration, and Chief of Staff chat.
 */

import type {
  ChatActRequest,
  ChatRequest,
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

// Re-export the generated DTO under a domain name so callers stay insulated
// from the generated barrel's layout; the source of truth is openapi.gen.ts.
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

// Org-alert wire shape. The backend serialises the durable ``Alert`` to a
// plain dict (not a named OpenAPI schema), so this interface is
// hand-maintained against ``_alert_to_dict`` in the meta-alerts controller.
export type AlertSeverity = 'info' | 'warning' | 'critical'
export type AlertType = 'inflection' | 'threshold' | 'trend'

export interface AlertSummary {
  id: string
  severity: AlertSeverity
  alert_type: AlertType
  description: string
  affected_domains: readonly string[]
  signal_context: Record<string, unknown>
  recommended_action: string | null
  emitted_at: string
}

// Signals wire shape. The backend serialises ``get_signals`` to a plain dict
// (``{"enabled": bool, "domains": [{"name", "status"}]}`` -- not a named
// OpenAPI schema), so this interface is hand-maintained against the
// ``get_signals`` handler in ``api/controllers/meta.py``; a new backend
// status literal must be added to ``SignalDomainStatus`` here in the same PR.
export type SignalDomainStatus = 'available' | 'unavailable'

export interface SignalDomain {
  name: string
  status: SignalDomainStatus
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

/**
 * Per-capability toggles for the conversational interface. These (not
 * the umbrella ``chief_of_staff_enabled`` flag, which governs the
 * meta-loop's chief-of-staff analysis role) are what the chat
 * endpoints live-gate on per request.
 */
export interface ChiefOfStaffFlags {
  chat_enabled: boolean
  propose_enabled: boolean
  group_chat_enabled: boolean
  direct_mcp_enabled: boolean
  // Per-capability model ids. Blank (``null``) until an operator or setup
  // selects one: an enabled capability with a blank model 503s server-side,
  // so the dashboard surfaces the missing setting inline before the request.
  chat_model: string | null
  propose_model: string | null
  routing_model: string | null
  narrative_model: string | null
  // Effective direct-MCP readiness. ``direct_mcp_enabled`` alone is inert:
  // the acting path stays fail-closed until security governance + the MCP
  // self-consumer are configured (a wired conversational actor). Lets the
  // dashboard cross-warn that an enabled toggle is not yet live.
  direct_mcp_ready: boolean
}

export interface MetaConfig {
  enabled: boolean
  chief_of_staff_enabled: boolean
  chief_of_staff?: ChiefOfStaffFlags
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

export interface AlertListFilter {
  severity?: AlertSeverity
  alertType?: AlertType
}

async function fetchAlertsPage(
  cursor: string | null,
  filter?: AlertListFilter,
): Promise<PaginatedResult<AlertSummary>> {
  const response = await apiClient.get<PaginatedResponse<AlertSummary>>(
    `${BASE}/alerts`,
    {
      params: {
        ..._pageParams(cursor),
        ...(filter?.severity ? { severity: filter.severity } : {}),
        ...(filter?.alertType ? { alert_type: filter.alertType } : {}),
      },
    },
  )
  return unwrapPaginated<AlertSummary>(response)
}

export async function listAlerts(filter?: AlertListFilter): Promise<AlertSummary[]> {
  return paginateAll<AlertSummary>((cursor) => fetchAlertsPage(cursor, filter))
}

/**
 * Fetch a single bounded page of the most recent alerts (backend default
 * page size, newest-first). For UI surfaces like the chat scope picker
 * that only need a reasonably-sized recent set, not the full history --
 * unlike {@link listAlerts}, this never walks every cursor page.
 */
export async function listRecentAlerts(
  filter?: AlertListFilter,
): Promise<AlertSummary[]> {
  return (await fetchAlertsPage(null, filter)).data
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
  idempotencyKey?: string,
): Promise<ConversationalProposeResponse> {
  const trimmed = message.trim()
  if (!trimmed) {
    throw new Error('Message must not be blank')
  }
  // The /meta/chat/propose endpoint is rate-limited via
  // ``per_op_rate_limit_from_policy("meta.chat.propose", key="user")``
  // (5 req / 60 s / user). Attach an Idempotency-Key so the axios 429
  // interceptor retries after Retry-After; server replays of the same
  // key are no-ops, so a retry never duplicates the parked proposal. A
  // caller-supplied key (a manual retry) reuses the original.
  const body: ConversationalProposeRequest = {
    message: trimmed,
    conversation_id: conversationId ?? null,
    project: project ?? null,
  }
  const response = await apiClient.post<
    ApiResponse<ConversationalProposeResponse>
  >(`${BASE}/chat/propose`, body, {
    headers: {
      'Idempotency-Key': idempotencyKey ?? crypto.randomUUID(),
    },
  })
  return unwrap(response)
}

export async function postChatGroup(
  message: string,
  agentIds: readonly string[],
  conversationId?: string,
  idempotencyKey?: string,
): Promise<GroupConverseResult> {
  const trimmed = message.trim()
  if (!trimmed) {
    throw new Error('Message must not be blank')
  }
  // The /meta/chat/group endpoint is rate-limited via
  // ``per_op_rate_limit_from_policy("meta.chat.group", key="user")``
  // (5 req / 60 s / user). Attach an Idempotency-Key so the axios 429
  // interceptor retries after Retry-After; a server replay of the same
  // key is a no-op, so a retry never double-runs a round. A caller-supplied
  // key (a manual retry of a turn) reuses the original key so a turn that
  // actually succeeded server-side is deduped rather than re-run.
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
        'Idempotency-Key': idempotencyKey ?? crypto.randomUUID(),
      },
    },
  )
  return unwrap(response)
}

export async function postChatAct(
  instruction: string,
  agent: string,
  conversationId?: string,
  idempotencyKey?: string,
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
  // key is a no-op, so a retry never double-runs an action. A caller-supplied
  // key (a manual retry) reuses the original so a succeeded action is deduped.
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
        'Idempotency-Key': idempotencyKey ?? crypto.randomUUID(),
      },
    },
  )
  return unwrap(response)
}

// A discriminated union, not two independent optionals: the picker UI
// (ChatScopeValue) can only ever produce "scoped to one proposal" or
// "scoped to one alert", never both, so the wire-adjacent type should
// make that illegal state unrepresentable too.
export type ChatScope =
  | { kind: 'proposal'; id: string }
  | { kind: 'alert'; id: string }

/** Summary of one of the caller's resumable conversations. */
export interface ConversationSummary {
  id: string
  created_by: string
  created_at: string
  updated_at: string
  status: string
  /** ``direct`` / ``routed`` resume into Request work; ``group`` into Group. */
  kind: string
}

/** One persisted turn of a conversation, for reconstructing a transcript. */
export interface ConversationTurnRecord {
  id: string
  conversation_id: string
  sequence: number
  role: string
  content: string
  author_agent_id: string | null
  author_name: string | null
  routed_topic: string | null
  routing_confidence: number | null
  created_at: string
}

async function fetchConversationsPage(
  cursor: string | null,
): Promise<PaginatedResult<ConversationSummary>> {
  const response = await apiClient.get<PaginatedResponse<ConversationSummary>>(
    `${BASE}/chat/conversations`,
    { params: _pageParams(cursor) },
  )
  return unwrapPaginated<ConversationSummary>(response)
}

/** List the caller's conversations (newest-first), walking every page. */
export async function listConversations(): Promise<ConversationSummary[]> {
  return paginateAll<ConversationSummary>(fetchConversationsPage)
}

/** Fetch every turn of one conversation (oldest-first), walking every page. */
export async function getConversationTurns(
  conversationId: string,
): Promise<ConversationTurnRecord[]> {
  const turns = await paginateAll<ConversationTurnRecord>((cursor) =>
    fetchConversationTurnsPage(conversationId, cursor),
  )
  return [...turns].sort((a, b) => a.sequence - b.sequence)
}

async function fetchConversationTurnsPage(
  conversationId: string,
  cursor: string | null,
): Promise<PaginatedResult<ConversationTurnRecord>> {
  const response = await apiClient.get<
    PaginatedResponse<ConversationTurnRecord>
  >(`${BASE}/chat/conversations/${encodeURIComponent(conversationId)}`, {
    params: _pageParams(cursor),
  })
  return unwrapPaginated<ConversationTurnRecord>(response)
}

export async function postChat(
  question: string,
  scope?: ChatScope,
  idempotencyKey?: string,
): Promise<ChatResponse> {
  const trimmed = question.trim()
  if (!trimmed) {
    throw new Error('Question must not be blank')
  }
  // The /meta/chat endpoint is guarded by
  // ``per_op_rate_limit_from_policy("meta.chat", key="user")``
  // (5 req / 60 s / user).  Attach an ``Idempotency-Key`` so the
  // axios 429 interceptor retries after ``Retry-After`` instead of
  // surfacing a hard failure on ratelimit bursts -- the server treats
  // replays of the same key as a no-op, so the retry is safe. A
  // caller-supplied key (a manual retry) reuses the original.
  const body: ChatRequest = {
    question: trimmed,
    proposal_id: scope?.kind === 'proposal' ? scope.id : null,
    alert_id: scope?.kind === 'alert' ? scope.id : null,
  }
  const response = await apiClient.post<ApiResponse<ChatResponse>>(
    `${BASE}/chat`,
    body,
    {
      headers: {
        'Idempotency-Key': idempotencyKey ?? crypto.randomUUID(),
      },
    },
  )
  return unwrap(response)
}
