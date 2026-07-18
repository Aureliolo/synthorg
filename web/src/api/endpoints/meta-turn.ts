/**
 * Client for the one unified conversational turn endpoint.
 *
 * The single front door for talking to the org: the server classifies the
 * message into a capability (explain / propose / act / group / charter) and
 * dispatches to the matching service, returning whichever payload the chosen
 * intent produced (all other payload slots are ``null``). One turn, one call.
 */

import { getCsrfToken } from '@/utils/csrf'
import { fetchWithRetryAfter } from '@/utils/fetch-with-retry'
import { parseRetryAfterMs, RateLimitedError } from '@/utils/retry-after'
import { apiClient, LLM_BOUND_TIMEOUT_MS, unwrap } from '../client'
import { readSseFrames } from '../sse/read-frames'
import type { ApiResponse } from '../types/http'
import type { ChimeIn, TurnIntent, TurnRequest, TurnResult } from '../types'

import { parseCitedRecords } from './cited-records'

const BASE = '/meta'

/** Build the wire body shared by the buffered and streaming turn calls. */
function buildTurnRequest(message: string, options?: PostTurnOptions): TurnRequest {
  return {
    message,
    conversation_id: options?.conversationId ?? null,
    intent_override: options?.intentOverride ?? null,
    project: options?.project ?? null,
  }
}

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
  const response = await apiClient.post<ApiResponse<TurnResult>>(
    `${BASE}/chat/turn`,
    buildTurnRequest(trimmed, options),
    turnRequestConfig(options),
  )
  return reparseTurnAnswer(unwrap(response))
}

/** A turn the stream refused to run: the client re-issues it buffered. */
export interface DeferredTurn {
  kind: 'deferred'
  /** The classified intent to re-issue with as an explicit override. */
  intent: TurnIntent
}

/** An EXPLAIN turn the stream answered live via the handlers below. */
export interface ExplainedTurn {
  kind: 'explained'
}

export type StreamTurnOutcome = DeferredTurn | ExplainedTurn

export interface StreamTurnHandlers {
  /** One content fragment of the streaming answer, in arrival order. */
  onDelta: (delta: string) => void
  /** The terminal result once the answer finishes streaming. */
  onComplete: (result: TurnResult) => void
  /** One specialist chime-in, delivered after the answer. */
  onChime: (chime: ChimeIn) => void
}

function _turnStreamUrl(): string {
  const base = apiClient.defaults.baseURL ?? ''
  return `${base}${BASE}/chat/turn/stream`
}

async function _openTurnStream(
  body: TurnRequest,
  signal: AbortSignal | undefined,
): Promise<Response> {
  const csrfToken = getCsrfToken()
  // The stream POST is safe to retry on 429: an EXPLAIN turn is read-only and a
  // non-EXPLAIN turn only classifies (it defers execution to the buffered,
  // idempotent endpoint), so re-opening the stream runs no side effect.
  const response = await fetchWithRetryAfter(
    _turnStreamUrl(),
    {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
      },
      body: JSON.stringify(body),
      ...(signal !== undefined && { signal }),
    },
    { idempotent: true },
  )
  if (!response.ok || !response.body) {
    if (response.status === 429) {
      const retryAfter = response.headers.get('retry-after') ?? undefined
      throw new RateLimitedError(parseRetryAfterMs(retryAfter, null))
    }
    throw new Error(`Turn stream failed: HTTP ${response.status}`)
  }
  return response
}

function _dispatchTurnFrame(
  event: string,
  data: string,
  handlers: StreamTurnHandlers,
  setDeferred: (intent: TurnIntent) => void,
): void {
  const payload: unknown = JSON.parse(data)
  switch (event) {
    case 'delta':
      handlers.onDelta((payload as { delta: string }).delta)
      return
    case 'complete':
      handlers.onComplete(reparseTurnAnswer(payload as TurnResult))
      return
    case 'chime':
      handlers.onChime(payload as ChimeIn)
      return
    case 'deferred':
      setDeferred((payload as { intent: TurnIntent }).intent)
      return
    case 'error':
      // The server emits an `sse_error`-shaped frame ({ error, status, ... }).
      throw new Error(
        (payload as { error?: string }).error ?? 'The org could not respond',
      )
    default:
      return
  }
}

/**
 * Stream one operator turn. An EXPLAIN turn streams live via `handlers`; any
 * other intent resolves to a {@link DeferredTurn} the caller re-issues through
 * {@link postTurn} with the returned intent as an override (so an acting turn
 * only ever runs on the buffered, idempotent endpoint).
 */
export async function streamTurn(
  message: string,
  handlers: StreamTurnHandlers,
  options?: PostTurnOptions,
): Promise<StreamTurnOutcome> {
  const trimmed = message.trim()
  if (!trimmed) {
    throw new Error('Message must not be blank')
  }
  const response = await _openTurnStream(
    buildTurnRequest(trimmed, options),
    options?.signal,
  )
  let deferredIntent: TurnIntent | null = null
  await readSseFrames(response, (event, data) => {
    _dispatchTurnFrame(event, data, handlers, (intent) => {
      deferredIntent = intent
    })
  })
  // ``deferredIntent`` is set inside the ``readSseFrames`` callback, invisible
  // to the flow analysis, which narrows it to ``null`` here.
  // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- mutated indirectly inside the frame callback
  return deferredIntent !== null
    ? { kind: 'deferred', intent: deferredIntent }
    : { kind: 'explained' }
}
