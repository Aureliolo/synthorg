import { useEffect, useMemo, useReducer } from 'react'
import { listMessages } from '@/api/endpoints/messages'
import { aggregateMessages, type CommunicationLink } from '@/pages/org/aggregate-messages'

/** Default time window for frequency calculation: 24 hours. */
const DEFAULT_TIME_WINDOW_MS = 24 * 60 * 60 * 1000

/** Maximum number of pages to fetch to avoid runaway pagination. */
const MAX_PAGES = 10

/** Page size for each API call. */
const PAGE_LIMIT = 100

export interface UseCommunicationEdgesReturn {
  links: CommunicationLink[]
  loading: boolean
  error: string | null
  /** True when MAX_PAGES was reached before all messages were fetched. */
  truncated: boolean
}

interface FetchState {
  messages: Array<{ sender: string; to: string }>
  loading: boolean
  error: string | null
  truncated: boolean
}

type FetchAction =
  | { type: 'reset' }
  | { type: 'loading' }
  | { type: 'success'; messages: Array<{ sender: string; to: string }>; truncated: boolean }
  | { type: 'error'; error: string }

function fetchReducer(_state: FetchState, action: FetchAction): FetchState {
  switch (action.type) {
    case 'reset':
      return { messages: [], loading: false, error: null, truncated: false }
    case 'loading':
      return { messages: [], loading: true, error: null, truncated: false }
    case 'success':
      return { messages: action.messages, loading: false, error: null, truncated: action.truncated }
    case 'error':
      return { messages: [], loading: false, error: action.error, truncated: false }
  }
}

interface CollectedMessages {
  readonly messages: Array<{ sender: string; to: string }>
  readonly truncated: boolean
}

/**
 * Walk up to MAX_PAGES of the messages endpoint into a flat sender/to
 * list. Returns null when the abort signal fires mid-walk so the caller
 * can skip the success dispatch.
 */
async function _collectAllMessages(
  signal: AbortSignal,
): Promise<CollectedMessages | null> {
  const messages: Array<{ sender: string; to: string }> = []
  let cursor: string | null = null
  let truncated = false
  for (let page = 0; page < MAX_PAGES; page++) {
    if (signal.aborted) return null
    const result = await listMessages({ cursor, limit: PAGE_LIMIT, signal })
    for (const msg of result.data) {
      messages.push({ sender: msg.sender, to: msg.to })
    }
    const exhausted = !result.hasMore || result.data.length === 0
    if (exhausted) break
    cursor = result.nextCursor
    if (page === MAX_PAGES - 1) truncated = true
  }
  return { messages, truncated }
}

function _toErrorMessage(err: unknown): string {
  return err instanceof Error ? err.message : 'Failed to fetch messages'
}

/**
 * Fetch inter-agent messages and aggregate into communication links.
 *
 * Fetches up to MAX_PAGES pages of messages, aggregates sender/receiver pairs,
 * and returns communication links with volume and frequency data.
 */
export function useCommunicationEdges(enabled = true): UseCommunicationEdgesReturn {
  const [state, dispatch] = useReducer(fetchReducer, {
    messages: [],
    loading: false,
    error: null,
    truncated: false,
  })

  useEffect(() => {
    if (!enabled) {
      dispatch({ type: 'reset' })
      return
    }
    const controller = new AbortController()
    dispatch({ type: 'loading' })
    async function fetchAll(): Promise<void> {
      try {
        const result = await _collectAllMessages(controller.signal)
        if (controller.signal.aborted || result === null) return
        dispatch({
          type: 'success',
          messages: result.messages,
          truncated: result.truncated,
        })
      } catch (err) {
        if (controller.signal.aborted) return
        dispatch({ type: 'error', error: _toErrorMessage(err) })
      }
    }
    void fetchAll()
    return () => { controller.abort() }
  }, [enabled])

  const links = useMemo(
    () => (state.messages.length > 0 ? aggregateMessages(state.messages, DEFAULT_TIME_WINDOW_MS) : []),
    [state.messages],
  )

  return { links, loading: state.loading, error: state.error, truncated: state.truncated }
}
