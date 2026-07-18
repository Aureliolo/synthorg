/**
 * Client for the one unified conversational turn endpoint.
 *
 * The single front door for talking to the org: the server classifies the
 * message into a capability (explain / propose / act / group / charter) and
 * dispatches to the matching service, returning whichever payload the chosen
 * intent produced (all other payload slots are ``null``). One turn, one call.
 */

import { apiClient, LLM_BOUND_TIMEOUT_MS, unwrap } from '../client'
import type { ApiResponse } from '../types/http'
import type { TurnIntent, TurnRequest, TurnResult } from '../types'

import { parseCitedRecords } from './cited-records'

const BASE = '/meta'

export interface PostTurnOptions {
  conversationId?: string
  /** Force a capability instead of letting the org classify the turn. */
  intentOverride?: TurnIntent
  project?: string
  idempotencyKey?: string
  signal?: AbortSignal
}

function turnRequestConfig(options: PostTurnOptions | undefined) {
  return {
    headers: {
      // The /meta/chat/turn endpoint is rate-limited (5 req / 60 s / user);
      // an Idempotency-Key lets the axios 429 interceptor retry after
      // Retry-After, and a server replay of the same key never
      // double-dispatches (critically, never re-runs an ACT turn's tools). A
      // caller-supplied key (a manual retry) reuses the original.
      'Idempotency-Key': options?.idempotencyKey ?? crypto.randomUUID(),
    },
    // LLM-bound: intent classification plus the dispatched agent session
    // regularly exceed the 30s client default.
    timeout: LLM_BOUND_TIMEOUT_MS,
    // Caller-supplied signal lets the operator abort a long-pending turn. The
    // server still completes and persists any work (the request is
    // idempotent), so aborting only detaches the client's wait.
    ...(options?.signal && { signal: options.signal }),
  }
}

// Re-validate the EXPLAIN answer's cited_records through the shared guard so
// the buffered turn enters the UI with the same defensively-parsed shape the
// standalone explain path produces.
function reparseTurnAnswer(result: TurnResult): TurnResult {
  if (!result.answer) return result
  return {
    ...result,
    answer: {
      ...result.answer,
      cited_records: parseCitedRecords(result.answer.cited_records),
    },
  }
}

/** Post one operator turn to the unified org conversation. */
export async function postTurn(
  message: string,
  options?: PostTurnOptions,
): Promise<TurnResult> {
  const trimmed = message.trim()
  if (!trimmed) {
    throw new Error('Message must not be blank')
  }
  const body: TurnRequest = {
    message: trimmed,
    conversation_id: options?.conversationId ?? null,
    intent_override: options?.intentOverride ?? null,
    project: options?.project ?? null,
  }
  const response = await apiClient.post<ApiResponse<TurnResult>>(
    `${BASE}/chat/turn`,
    body,
    turnRequestConfig(options),
  )
  return reparseTurnAnswer(unwrap(response))
}
