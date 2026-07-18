/**
 * Meta improvement API endpoints.
 *
 * Provides access to improvement proposals, signal domains,
 * A/B tests, configuration, and Chief of Staff chat.
 */

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

export type { CitedRecord } from './cited-records'
// The unified conversational turn lives in its own module to keep this file
// under its size budget; re-exported here so callers keep one import surface.
export {
  postTurn,
  streamTurn,
  type PostTurnOptions,
  type StreamTurnHandlers,
  type StreamTurnOutcome,
} from './meta-turn'

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

/** One of the three conversation shapes (mirrors backend ``ConversationKind``). */
export type ConversationKind = 'direct' | 'routed' | 'group'

/** Conversation lifecycle state (mirrors backend ``ConversationStatus``). */
export type ConversationStatus = 'active' | 'proposed' | 'closed'

/** Turn author role (mirrors backend ``ConversationRole``). */
export type ConversationTurnRole = 'user' | 'assistant' | 'agent'

/** Summary of one of the caller's resumable conversations. */
export interface ConversationSummary {
  id: string
  created_by: string
  created_at: string
  updated_at: string
  status: ConversationStatus
  /** ``direct`` / ``routed`` resume into Request work; ``group`` into Group. */
  kind: ConversationKind
}

/** One persisted turn of a conversation, for reconstructing a transcript. */
export interface ConversationTurnRecord {
  id: string
  conversation_id: string
  sequence: number
  role: ConversationTurnRole
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
