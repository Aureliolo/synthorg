/**
 * SSE consumers for the streaming Chief-of-Staff endpoints.
 *
 * Uses fetch + ReadableStream (not EventSource, which is GET-only) so the
 * POST body carries the question / instruction. Both endpoints speak the
 * same ``progress`` / ``complete`` / ``error`` frame convention as the
 * model-pull stream; an ``error`` frame throws so the caller's store
 * surfaces it through the normal mutation-error path.
 */

import { createLogger } from '@/lib/logger'
import { getCsrfToken } from '@/utils/csrf'

import { apiClient } from '../client'
import type { ConversationalActResult } from '../types'

const log = createLogger('meta-stream')

interface SseFrame {
  event: string
  data: unknown
}

interface SseCarry {
  event: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parseSseLines(
  lines: readonly string[],
  carry: SseCarry,
  emit: (frame: SseFrame) => void,
): void {
  for (const line of lines) {
    if (line.startsWith('event: ')) {
      carry.event = line.slice(7).trim()
      continue
    }
    if (line.startsWith('data: ')) {
      const raw = line.slice(6)
      let data: unknown
      try {
        data = JSON.parse(raw)
      } catch {
        log.warn('Malformed JSON in meta stream line')
        carry.event = ''
        continue
      }
      emit({ event: carry.event || 'message', data })
      carry.event = ''
    }
  }
}

async function openStream(
  path: string,
  body: unknown,
  signal: AbortSignal | undefined,
): Promise<Response> {
  const baseUrl = apiClient.defaults.baseURL ?? ''
  const csrfToken = getCsrfToken()
  const response = await fetch(`${baseUrl}${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
    },
    body: JSON.stringify(body),
    ...(signal !== undefined && { signal }),
  })
  if (!response.ok || !response.body) {
    throw new Error(`Stream failed: HTTP ${response.status}`)
  }
  return response
}

async function consumeStream(
  response: Response,
  emit: (frame: SseFrame) => void,
): Promise<void> {
  const body = response.body
  if (!body) throw new Error('Expected a streaming response body')
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const carry: SseCarry = { event: '' }
  // Track a clean end-of-stream so the ``finally`` only cancels the reader
  // when the loop exited early (abort / an ``error`` frame throwing); the
  // active-handle gate requires the lock released on every path.
  let finished = false
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) {
        finished = true
        break
      }
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      parseSseLines(lines, carry, emit)
    }
    buffer += decoder.decode()
    if (buffer.trim()) parseSseLines(buffer.split('\n'), carry, emit)
  } finally {
    if (!finished) await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}

function errorMessage(data: unknown): string {
  if (isRecord(data) && typeof data['error'] === 'string') return data['error']
  return 'The stream failed.'
}

/** Assembled result of a completed streaming chat answer. */
export interface ChatStreamResult {
  answer: string
  sources: string[]
  confidence: number
}

/** Incremental callbacks for a streaming Chief-of-Staff answer. */
export interface ChatStreamCallbacks {
  onDelta: (delta: string) => void
  onComplete: (result: ChatStreamResult) => void
}

function parseChatComplete(data: unknown): ChatStreamResult {
  if (!isRecord(data)) return { answer: '', sources: [], confidence: 0.5 }
  const sources = data['sources']
  return {
    answer: typeof data['answer'] === 'string' ? data['answer'] : '',
    sources: Array.isArray(sources)
      ? sources.filter((s): s is string => typeof s === 'string')
      : [],
    confidence: typeof data['confidence'] === 'number' ? data['confidence'] : 0.5,
  }
}

/** Stream a free-form Chief-of-Staff answer token-by-token. */
export async function streamChatAnswer(
  question: string,
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const response = await openStream('/meta/chat/stream', { question }, signal)
  await consumeStream(response, (frame) => {
    if (frame.event === 'progress') {
      if (isRecord(frame.data) && typeof frame.data['delta'] === 'string') {
        callbacks.onDelta(frame.data['delta'])
      }
      return
    }
    if (frame.event === 'complete') {
      callbacks.onComplete(parseChatComplete(frame.data))
      return
    }
    if (frame.event === 'error') {
      throw new Error(errorMessage(frame.data))
    }
  })
}

/** Request body for a streaming direct action (mirrors the buffered POST). */
export interface ActStreamRequest {
  instruction: string
  agent: string
  conversation_id?: string | null
}

/** Incremental callbacks for a streaming direct action. */
export interface ActStreamCallbacks {
  onProgress: (turn: number, tools: string[]) => void
  onComplete: (result: ConversationalActResult) => void
}

/** Stream a direct MCP action's per-turn progress, then its result. */
export async function streamChatAct(
  body: ActStreamRequest,
  callbacks: ActStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const response = await openStream('/meta/chat/act/stream', body, signal)
  await consumeStream(response, (frame) => {
    if (frame.event === 'progress') {
      if (isRecord(frame.data)) {
        const rawTurn = frame.data['turn']
        const rawTools = frame.data['tools']
        const turn = typeof rawTurn === 'number' ? rawTurn : 0
        const tools = Array.isArray(rawTools)
          ? rawTools.filter((t): t is string => typeof t === 'string')
          : []
        callbacks.onProgress(turn, tools)
      }
      return
    }
    if (frame.event === 'complete') {
      callbacks.onComplete(frame.data as ConversationalActResult)
      return
    }
    if (frame.event === 'error') {
      throw new Error(errorMessage(frame.data))
    }
  })
}
