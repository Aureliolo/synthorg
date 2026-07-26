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
import { ApiRequestError, apiClient, LLM_BOUND_TIMEOUT_MS, unwrap } from '../client'
import { readSseFrames } from '../sse/read-frames'
import type { ApiResponse } from '../types/http'
import type { ErrorDetail } from '@/api/types/errors'
import type {
  ChimeIn,
  TurnIntent,
  TurnRequest,
  TurnResult,
} from '@/api/types/meta-turn'

import { parseCitedRecords } from './cited-records'

const BASE = '/meta'

/** Build the wire body shared by the buffered and streaming turn calls. */
function buildTurnRequest(message: string, options?: PostTurnOptions): TurnRequest {
  const opts = options ?? {}
  return {
    message,
    conversation_id: opts.conversationId ?? null,
    intent_override: opts.intentOverride ?? null,
    named_targets: opts.namedTargets ?? [],
    project: opts.project ?? null,
    connection_draft_id: opts.connectionDraftId ?? null,
    provided_credential_handles: opts.providedCredentialHandles ?? {},
  }
}

export interface PostTurnOptions {
  conversationId?: string
  /** Force a capability instead of letting the org classify the turn. */
  intentOverride?: TurnIntent
  /**
   * Roles/names the stream classified for an ACT/GROUP turn, carried into the
   * buffered reissue so those turns keep their targets instead of degrading to
   * EXPLAIN. Only honoured alongside {@link intentOverride}.
   */
  namedTargets?: readonly string[]
  project?: string
  idempotencyKey?: string
  signal?: AbortSignal
  /** Operator-console setup draft to continue (a CONFIGURE follow-up turn). */
  connectionDraftId?: string
  /**
   * Opaque single-use secret-capture handles (field-name -> handle) the
   * operator provided out of band; only meaningful on a CONFIGURE turn. The raw
   * secret is never sent here.
   */
  providedCredentialHandles?: Readonly<Record<string, string>>
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
  /** The classified ACT/GROUP targets to carry into the buffered reissue. */
  namedTargets: string[]
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

// Decode a non-OK stream response into a structured `ApiRequestError` so a
// fail-closed 503 (or any domain error) surfaces its `error_code` / detail to
// `describeConversationalError`, the same as the buffered turn path, instead of
// a bare `Error` that loses the server's reason.
async function _decodeStreamError(response: Response): Promise<ApiRequestError> {
  let detail: ErrorDetail | null = null
  let message = `Turn stream failed: HTTP ${response.status}`
  try {
    const parsed = (await response.json()) as {
      error?: string
      error_detail?: ErrorDetail | null
    }
    detail = parsed.error_detail ?? null
    message = detail?.detail || parsed.error || message
  } catch {
    // A non-JSON body (proxy error page, empty) keeps the generic message.
  }
  return new ApiRequestError(message, detail)
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
    throw await _decodeStreamError(response)
  }
  return response
}

// An in-stream `error` frame reconstructs an `ApiRequestError`: the server emits
// { error, error_detail? }, and a domain error carries the structured detail so
// the stream path surfaces the same error UX as the buffered turn (fail-closed
// 503 messaging, retry hints) via `getErrorDetail` / `describeConversationalError`.
function _throwStreamFrameError(payload: unknown): never {
  const frame = payload as { error?: string; error_detail?: ErrorDetail | null }
  throw new ApiRequestError(
    frame.error ?? 'The org could not respond',
    frame.error_detail ?? null,
  )
}

function _dispatchTurnFrame(
  event: string,
  data: string,
  handlers: StreamTurnHandlers,
  setDeferred: (intent: TurnIntent, namedTargets: string[]) => void,
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
    case 'deferred': {
      const frame = payload as { intent: TurnIntent; named_targets?: string[] }
      setDeferred(frame.intent, frame.named_targets ?? [])
      return
    }
    case 'error':
      _throwStreamFrameError(payload)
      break
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
  let deferredTargets: string[] = []
  let completed = false
  await readSseFrames(response, (event, data) => {
    _dispatchTurnFrame(
      event,
      data,
      {
        ...handlers,
        onComplete: (result) => {
          completed = true
          handlers.onComplete(result)
        },
      },
      (intent, namedTargets) => {
        deferredIntent = intent
        deferredTargets = namedTargets
      },
    )
  })
  // ``deferredIntent`` / ``completed`` are set inside the ``readSseFrames``
  // callback, invisible to the flow analysis, which narrows them here.
  /* eslint-disable @typescript-eslint/no-unnecessary-condition -- mutated indirectly inside the frame callback */
  if (deferredIntent !== null) {
    return { kind: 'deferred', intent: deferredIntent, namedTargets: deferredTargets }
  }
  if (!completed) {
    // A stream that ends with neither a ``complete`` nor a ``deferred`` frame
    // was truncated (dropped connection, proxy cut); surface it rather than
    // silently reporting a successful explained turn.
    throw new Error('Turn stream ended before a terminal frame')
  }
  return { kind: 'explained' }
  /* eslint-enable @typescript-eslint/no-unnecessary-condition */
}
