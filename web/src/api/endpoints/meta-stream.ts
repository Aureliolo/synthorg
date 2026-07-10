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
import { sanitizeForLog } from '@/utils/logging'

import { apiClient } from '../client'
import type { ChatStreamRequest } from '../types'

import type { CitedRecord } from './meta'

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
        log.warn('Malformed JSON in meta stream frame', {
          event: sanitizeForLog(carry.event || 'message'),
          length: raw.length,
        })
        carry.event = ''
        continue
      }
      emit({ event: carry.event || 'message', data })
      carry.event = ''
    }
  }
}

async function readErrorDetail(response: Response): Promise<string | null> {
  try {
    const parsed: unknown = await response.json()
    if (isRecord(parsed)) {
      // RFC 9457 body carries ``detail``; the legacy error envelope
      // carries ``error``. Prefer whichever the failing endpoint sent.
      const detail = parsed['detail']
      if (typeof detail === 'string' && detail.trim()) return detail
      const error = parsed['error']
      if (typeof error === 'string' && error.trim()) return error
    }
  } catch {
    // Non-JSON or empty body: fall back to the status-only message.
  }
  return null
}

async function openStream(
  path: string,
  body: ChatStreamRequest,
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
    // Surface the endpoint's own message (e.g. the 503 "Chief of Staff
    // chat is not configured..." detail) instead of an opaque status.
    const detail = await readErrorDetail(response)
    throw new Error(detail ?? `Stream failed: HTTP ${response.status}`)
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
  citedRecords: CitedRecord[]
  confidence: number
}

const CITED_KINDS = new Set<CitedRecord['kind']>(['task', 'project', 'approval'])

function isCitedRecord(entry: unknown): entry is CitedRecord {
  return (
    isRecord(entry) &&
    typeof entry['kind'] === 'string' &&
    CITED_KINDS.has(entry['kind'] as CitedRecord['kind']) &&
    typeof entry['record_id'] === 'string' &&
    typeof entry['label'] === 'string' &&
    typeof entry['status'] === 'string'
  )
}

/**
 * Validate a wire `cited_records` array, dropping (and warning on) any entry
 * that doesn't match the contract. Shared by the streaming complete frame and
 * the buffered `postChat` response so both enter the UI through one guard.
 */
export function parseCitedRecords(value: unknown): CitedRecord[] {
  if (!Array.isArray(value)) return []
  const records: CitedRecord[] = []
  for (const entry of value) {
    if (isCitedRecord(entry)) {
      records.push(entry)
    } else {
      log.warn('Dropping malformed cited_record entry', sanitizeForLog(entry))
    }
  }
  return records
}

/** Incremental callbacks for a streaming Chief-of-Staff answer. */
export interface ChatStreamCallbacks {
  onDelta: (delta: string) => void
  onComplete: (result: ChatStreamResult) => void
}

function parseChatComplete(data: unknown): ChatStreamResult {
  if (!isRecord(data)) {
    log.warn('Malformed complete frame in chat stream; using empty defaults')
    return { answer: '', sources: [], citedRecords: [], confidence: 0.5 }
  }
  const sources = data['sources']
  return {
    answer: typeof data['answer'] === 'string' ? data['answer'] : '',
    sources: Array.isArray(sources)
      ? sources.filter((s): s is string => typeof s === 'string')
      : [],
    citedRecords: parseCitedRecords(data['cited_records']),
    confidence: typeof data['confidence'] === 'number' ? data['confidence'] : 0.5,
  }
}

/** Stream a free-form Chief-of-Staff answer token-by-token. */
export async function streamChatAnswer(
  question: string,
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const body: ChatStreamRequest = { question }
  const response = await openStream('/meta/chat/stream', body, signal)
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
