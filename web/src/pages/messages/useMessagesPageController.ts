import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useSearchParams } from 'react-router'
import { useMessagesData } from '@/hooks/useMessagesData'
import { useMessagesStore } from '@/stores/messages'
import { filterMessages, type MessagePageFilters } from '@/utils/messages'
import {
  MESSAGE_PRIORITY_VALUES,
  MESSAGE_TYPE_VALUES,
  type Message,
  type MessagePriority,
  type MessageType,
} from '@/api/types/messages'

// Derive from the generated enum tuples so new MessageType / MessagePriority
// members added to the Python source flow through automatically; the old
// hand-maintained sets silently rejected any new enum value until someone
// updated this file too.
const VALID_TYPES: ReadonlySet<string> = new Set(MESSAGE_TYPE_VALUES)
const VALID_PRIORITIES: ReadonlySet<string> = new Set(MESSAGE_PRIORITY_VALUES)
const NEW_MESSAGE_FLASH_MS = 2000

export interface MessagesPageController {
  data: ReturnType<typeof useMessagesData>
  activeChannel: string | null
  filters: MessagePageFilters
  hasFilters: boolean
  filtered: readonly Message[]
  selectedMessageId: string | null
  selectedMessage: Message | null
  showOfflineBanner: boolean
  showInitialSkeleton: boolean
  handleFiltersChange: (filters: MessagePageFilters) => void
  handleSelectChannel: (name: string) => void
  handleSelectMessage: (id: string) => void
  handleCloseDrawer: () => void
}

export function useMessagesPageController(): MessagesPageController {
  const [searchParams, setSearchParams] = useSearchParams()
  const wasConnectedRef = useRef(false)
  const activeChannel = searchParams.get('channel')
  const data = useMessagesData(activeChannel)

  // Latching the "have we ever connected?" signal in an effect rather than in
  // the render body avoids a concurrent-mode hazard: render-phase ref writes
  // happen even on discarded renders, so a transient flicker could falsely
  // latch the flag. The effect only runs after commit.
  useEffect(() => {
    if (data.wsConnected) wasConnectedRef.current = true
  }, [data.wsConnected])

  useClearNewMessageFlashes(data.newMessageIds)

  const filters = useMemo<MessagePageFilters>(
    () => parseFiltersFromSearchParams(searchParams),
    [searchParams],
  )

  const handleFiltersChange = useCallback(
    (next: MessagePageFilters) =>
      setSearchParams((prev) => applyFilterParams(prev, next)),
    [setSearchParams],
  )

  const handleSelectChannel = useCallback(
    (name: string) => setSearchParams((prev) => applyChannelParam(prev, name)),
    [setSearchParams],
  )

  const selectedMessageId = searchParams.get('message')
  const selectedMessage = useMemo(
    () => data.messages.find((m) => m.id === selectedMessageId) ?? null,
    [data.messages, selectedMessageId],
  )

  const handleSelectMessage = useCallback(
    (id: string) =>
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        next.set('message', id)
        return next
      }),
    [setSearchParams],
  )

  const handleCloseDrawer = useCallback(
    () =>
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        next.delete('message')
        return next
      }),
    [setSearchParams],
  )

  const filtered = useMemo(
    () => filterMessages(data.messages, filters),
    [data.messages, filters],
  )
  const hasFilters = computeHasFilters(filters)
  const showOfflineBanner = computeShowOfflineBanner(
    data.wsSetupError,
    wasConnectedRef.current,
    data.wsConnected,
    data.loading,
  )
  const showInitialSkeleton = computeShowInitialSkeleton(data)

  return {
    data,
    activeChannel,
    filters,
    hasFilters,
    filtered,
    selectedMessageId,
    selectedMessage,
    showOfflineBanner,
    showInitialSkeleton,
    handleFiltersChange,
    handleSelectChannel,
    handleSelectMessage,
    handleCloseDrawer,
  }
}

function useClearNewMessageFlashes(newMessageIds: ReadonlySet<string>): void {
  useEffect(() => {
    if (newMessageIds.size === 0) return
    const timer = setTimeout(() => {
      useMessagesStore.getState().clearNewMessageIds()
    }, NEW_MESSAGE_FLASH_MS)
    return () => clearTimeout(timer)
  }, [newMessageIds])
}

function parseFiltersFromSearchParams(searchParams: URLSearchParams): MessagePageFilters {
  const rawType = searchParams.get('type')
  const rawPriority = searchParams.get('priority')
  return {
    type: rawType && VALID_TYPES.has(rawType) ? (rawType as MessageType) : undefined,
    priority:
      rawPriority && VALID_PRIORITIES.has(rawPriority)
        ? (rawPriority as MessagePriority)
        : undefined,
    search: searchParams.get('search') ?? undefined,
  }
}

function applyFilterParams(
  prev: URLSearchParams,
  newFilters: MessagePageFilters,
): URLSearchParams {
  const next = new URLSearchParams(prev)
  next.delete('type')
  next.delete('priority')
  next.delete('search')
  if (newFilters.type) next.set('type', newFilters.type)
  if (newFilters.priority) next.set('priority', newFilters.priority)
  if (newFilters.search) next.set('search', newFilters.search)
  return next
}

function applyChannelParam(prev: URLSearchParams, name: string): URLSearchParams {
  const next = new URLSearchParams(prev)
  next.set('channel', name)
  next.delete('message')
  next.delete('type')
  next.delete('priority')
  next.delete('search')
  return next
}

function computeHasFilters(filters: MessagePageFilters): boolean {
  return Boolean(filters.type || filters.priority || filters.search)
}

function computeShowOfflineBanner(
  wsSetupError: string | null,
  hasEverConnected: boolean,
  wsConnected: boolean,
  loading: boolean,
): boolean {
  if (loading) return false
  return Boolean(wsSetupError) || (hasEverConnected && !wsConnected)
}

function computeShowInitialSkeleton(data: ReturnType<typeof useMessagesData>): boolean {
  if (!data.loading) return false
  if (data.messages.length !== 0) return false
  if (!data.channelsLoading) return false
  return data.channels.length === 0
}
