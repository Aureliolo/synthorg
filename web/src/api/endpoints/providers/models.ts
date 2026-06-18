import { createLogger } from '@/lib/logger'
import { getCsrfToken } from '@/utils/csrf'
import { IS_DEV_AUTH_BYPASS } from '@/utils/dev'
import { fetchWithRetryAfter } from '@/utils/fetch-with-retry'
import { parseRetryAfterMs, RateLimitedError } from '@/utils/retry-after'
import { apiClient, unwrap, unwrapVoid } from '../../client'
import type {
  AddModelRequest,
  ApiResponse,
  LocalModelParams,
  ProviderConfig,
  ProviderModelResponse,
  PullModelRequest,
  PullProgressEvent,
  SyncModelsRequest,
  SyncModelsResponse,
  UpdateModelConfigRequest,
} from '@/api/types'

const log = createLogger('providers-api-models')

/** Encode a model ID for use in URL paths, preserving `/` for :path params. */
function encodeModelIdPath(modelId: string): string {
  return modelId.split('/').map(encodeURIComponent).join('/')
}

type SseState = { currentEvent: string }
type SseLine =
  | { kind: 'event'; name: string }
  | { kind: 'data'; raw: string }
  | { kind: 'other' }

function _parseSseLine(line: string): SseLine {
  if (line.startsWith('event: ')) return { kind: 'event', name: line.slice(7).trim() }
  if (line.startsWith('data: ')) return { kind: 'data', raw: line.slice(6) }
  return { kind: 'other' }
}

function _decodeSsePayload(raw: string): PullProgressEvent | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    log.warn('Malformed JSON in pull stream line')
    return null
  }
  // Reject non-objects before casting: the dispatcher reads
  // ``error`` / ``status`` off the payload, so a bare string / number /
  // array from a malformed stream must not slip through as an event.
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    log.warn('Unexpected non-object payload in pull stream line')
    return null
  }
  return parsed as PullProgressEvent
}

function _dispatchSseEvent(
  payload: PullProgressEvent,
  state: SseState,
  onProgress: (event: PullProgressEvent) => void,
): void {
  const isError = state.currentEvent === 'error' || Boolean(payload.error)
  state.currentEvent = ''
  onProgress(payload)
  if (isError) {
    const message = payload.error || payload.status || 'Pull failed'
    throw new Error(message)
  }
}

/** Process buffered SSE lines, dispatching events to the callback. */
function processSseLines(
  lines: string[],
  state: SseState,
  onProgress: (event: PullProgressEvent) => void,
): void {
  for (const line of lines) {
    const parsed = _parseSseLine(line)
    if (parsed.kind === 'event') {
      state.currentEvent = parsed.name
      continue
    }
    if (parsed.kind === 'data') {
      const payload = _decodeSsePayload(parsed.raw)
      if (payload) _dispatchSseEvent(payload, state, onProgress)
    }
  }
}

async function _handlePullUnauthorized(): Promise<void> {
  if (IS_DEV_AUTH_BYPASS) return
  // Split the dynamic-import failure from the store call so the store
  // keeps ownership of its own error UX (per the Zustand mutation
  // contract in web/CLAUDE.md: "Callers MUST NOT wrap store mutation
  // calls in try/catch"). The import failure path falls back to a
  // hard redirect; the store call is invoked outside this try so any
  // failure surfaces normally instead of being swallowed.
  let mod: typeof import('@/stores/auth')
  try {
    mod = await import('@/stores/auth')
  } catch (importErr: unknown) {
    log.error('Auth store cleanup failed during SSE 401 handling', importErr)
    if (window.location.pathname !== '/login' && window.location.pathname !== '/setup') {
      window.location.href = '/login'
    }
    return
  }
  mod.useAuthStore.getState().handleUnauthorized()
}

async function _raisePullFailure(response: Response): Promise<never> {
  if (response.status === 401) await _handlePullUnauthorized()
  if (response.status === 429) {
    // The retry budget is exhausted; surface a typed rate-limit error
    // carrying the server's Retry-After hint so the caller can show a
    // precise back-off instead of a generic HTTP 429.
    const retryAfter = response.headers.get('retry-after') ?? undefined
    throw new RateLimitedError(parseRetryAfterMs(retryAfter, null))
  }
  throw new Error(`Pull failed: HTTP ${response.status}`)
}

async function _openPullStream(
  name: string,
  modelName: string,
  signal: AbortSignal | undefined,
): Promise<Response> {
  const baseUrl = apiClient.defaults.baseURL ?? ''
  const url = `${baseUrl}/providers/${encodeURIComponent(name)}/models/pull`
  const csrfToken = getCsrfToken()
  // Pull-model is a long-running SSE stream but the *initial* POST that
  // opens the stream is safe to retry on 429. Each call resolves the
  // model once on the server (idempotent re-pull behaviour) so we opt
  // into retry explicitly even though POST is non-idempotent by default.
  const response = await fetchWithRetryAfter(
    url,
    {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
      },
      body: JSON.stringify({ model_name: modelName } satisfies PullModelRequest),
      ...(signal !== undefined && { signal }),
    },
    { idempotent: true },
  )

  if (!response.ok || !response.body) {
    await _raisePullFailure(response)
  }
  return response
}

async function _consumePullStream(
  response: Response,
  onProgress: (event: PullProgressEvent) => void,
): Promise<void> {
  const body = response.body
  if (!body) throw new Error('Expected a streaming response body')
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let receivedDone = false
  let completed = false
  const sseState: SseState = { currentEvent: '' }
  const dispatch = (event: PullProgressEvent): void => {
    if (event.done) receivedDone = true
    onProgress(event)
  }

  // The reader holds a lock on the underlying ReadableStream that the
  // active-handle gate (web/test-infra/active-handle-tracker.ts) tracks
  // as an HTTPCLIENTREQUEST / HTTP2STREAM resource attributable to
  // ``web/src/``. ``onProgress`` or a malformed payload's
  // ``_dispatchSseEvent`` ``throw`` can exit the loop before the
  // server signals done; without an explicit cancel/release the
  // stream would leak across tests. Cancel-on-incomplete + always
  // release covers both clean and error paths.
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      processSseLines(lines, sseState, dispatch)
    }

    buffer += decoder.decode()
    if (buffer.trim()) {
      processSseLines(buffer.split('\n'), sseState, dispatch)
    }

    // ``receivedDone`` is flipped inside the ``dispatch`` closure (invoked via
    // ``processSseLines``); the flow analysis cannot see that indirect mutation
    // and narrows it to ``false`` here.
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- mutated indirectly inside the dispatch closure
    if (!receivedDone) {
      throw new Error('Pull stream ended without completion event')
    }
    completed = true
  } finally {
    if (!completed) {
      await reader.cancel().catch(() => undefined)
    }
    reader.releaseLock()
  }
}

/**
 * Pull a model on a local provider via SSE streaming.
 *
 * Uses fetch + ReadableStream because the endpoint is POST-based
 * and EventSource only supports GET.
 */
export async function pullModel(
  name: string,
  modelName: string,
  onProgress: (event: PullProgressEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await _openPullStream(name, modelName, signal)
  await _consumePullStream(response, onProgress)
}

export async function deleteModel(name: string, modelId: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(
    `/providers/${encodeURIComponent(name)}/models/${encodeModelIdPath(modelId)}`,
  )
  unwrapVoid(response)
}

export async function updateModelConfig(
  name: string,
  modelId: string,
  params: LocalModelParams,
): Promise<ProviderModelResponse> {
  const payload: UpdateModelConfigRequest = { local_params: params }
  const response = await apiClient.put<ApiResponse<ProviderModelResponse>>(
    `/providers/${encodeURIComponent(name)}/models/${encodeModelIdPath(modelId)}/config`,
    payload,
  )
  return unwrap(response)
}

export async function addProviderModel(
  name: string,
  data: AddModelRequest,
): Promise<ProviderConfig> {
  const response = await apiClient.post<ApiResponse<ProviderConfig>>(
    `/providers/${encodeURIComponent(name)}/models`,
    data,
  )
  return unwrap(response)
}

export async function syncProviderModels(
  name: string,
  data: SyncModelsRequest,
): Promise<SyncModelsResponse> {
  const response = await apiClient.post<ApiResponse<SyncModelsResponse>>(
    `/providers/${encodeURIComponent(name)}/models/sync`,
    data,
  )
  return unwrap(response)
}
