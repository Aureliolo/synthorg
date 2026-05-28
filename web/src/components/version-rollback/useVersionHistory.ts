/** Hook owning the pagination state machine for version-history sections. */

import { useCallback, useEffect, useRef, useState } from 'react'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import type {
  ReadOnlyVersionHistoryClient,
  VersionSnapshot,
} from '@/api/endpoints/version-history'

const log = createLogger('version-history-section')

const PAGE_SIZE = 25

export interface VersionHistoryState<T> {
  items: readonly VersionSnapshot<T>[]
  cursor: string | null
  hasMore: boolean
  loading: boolean
  loadingMore: boolean
  error: string | null
  selectedVersion: number | null
  diffOpen: boolean
  diffPair: { from: number; to: number } | null
  rollbackOpen: boolean
}

export interface VersionHistoryHandle<T> extends VersionHistoryState<T> {
  findById: (id: string) => VersionSnapshot<T> | undefined
  diffFrom: number | null
  diffTo: number | null
  loadMore: () => Promise<void>
  refresh: () => Promise<void>
  select: (item: VersionSnapshot<T>) => void
  clearSelection: () => void
  openRollback: () => void
  closeRollback: () => void
  closeDiff: () => void
}

interface HistoryStateSlots<T> {
  items: readonly VersionSnapshot<T>[]
  setItems: React.Dispatch<React.SetStateAction<readonly VersionSnapshot<T>[]>>
  cursor: string | null
  setCursor: (cursor: string | null) => void
  hasMore: boolean
  setHasMore: (hasMore: boolean) => void
  loading: boolean
  setLoading: (loading: boolean) => void
  loadingMore: boolean
  setLoadingMore: (loadingMore: boolean) => void
  error: string | null
  setError: (error: string | null) => void
  selectedVersion: number | null
  setSelectedVersion: (version: number | null) => void
  diffOpen: boolean
  setDiffOpen: (open: boolean) => void
  diffPair: { from: number; to: number } | null
  setDiffPair: (pair: { from: number; to: number } | null) => void
  rollbackOpen: boolean
  setRollbackOpen: (open: boolean) => void
}

function useHistoryStateSlots<T>(): HistoryStateSlots<T> {
  const [items, setItems] = useState<readonly VersionSnapshot<T>[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)
  const [diffOpen, setDiffOpen] = useState(false)
  const [diffPair, setDiffPair] = useState<{ from: number; to: number } | null>(null)
  const [rollbackOpen, setRollbackOpen] = useState(false)
  return {
    items, setItems, cursor, setCursor, hasMore, setHasMore,
    loading, setLoading, loadingMore, setLoadingMore, error, setError,
    selectedVersion, setSelectedVersion, diffOpen, setDiffOpen,
    diffPair, setDiffPair, rollbackOpen, setRollbackOpen,
  }
}

function _resetState<T>(s: HistoryStateSlots<T>): void {
  s.setLoading(true)
  // Reset ``loadingMore`` on every new epoch. An in-flight loadMore()
  // whose epoch advanced will skip its own ``finally`` (intentional:
  // it must not commit stale data), but that leaves the spinner stuck
  // unless the new load clears it explicitly.
  s.setLoadingMore(false)
  s.setError(null)
  s.setItems([])
  s.setCursor(null)
  s.setHasMore(false)
  s.setSelectedVersion(null)
  s.setDiffOpen(false)
  s.setDiffPair(null)
  s.setRollbackOpen(false)
}

function _applyPage<T>(
  s: HistoryStateSlots<T>,
  page: { data: readonly VersionSnapshot<T>[]; nextCursor: string | null; hasMore: boolean },
): void {
  s.setItems(page.data)
  s.setCursor(page.nextCursor)
  s.setHasMore(page.hasMore)
}

function _diffPairFor(selectedVersion: number, version: number): { from: number; to: number } {
  return {
    from: Math.min(selectedVersion, version),
    to: Math.max(selectedVersion, version),
  }
}

function _selectVersion<T>(
  s: HistoryStateSlots<T>,
  item: VersionSnapshot<T>,
): void {
  // Two-click compare: first click selects the "from" version,
  // second click on a DIFFERENT version opens the diff drawer.
  // Clicking the same version twice CLEARS the selection: we do not
  // open a no-op diff against itself.
  if (s.selectedVersion === null) {
    s.setSelectedVersion(item.version)
    return
  }
  if (s.selectedVersion === item.version) {
    s.setSelectedVersion(null)
    return
  }
  s.setDiffPair(_diffPairFor(s.selectedVersion, item.version))
  s.setDiffOpen(true)
}

export function useVersionHistory<T>(
  client: ReadOnlyVersionHistoryClient<T>,
): VersionHistoryHandle<T> {
  const s = useHistoryStateSlots<T>()
  // Stable mirror of the slot bag so ``refresh`` / ``loadMore`` can be
  // memoised with deps that don't change every render. ``s`` itself is
  // a fresh object literal on each render (the values inside it move
  // when state updates), but every setter inside is stable by React's
  // contract. Mirroring ``s`` through a ref means callbacks reach for
  // the latest setters/values at invocation time without bringing the
  // bag identity into their dep list -- otherwise ``refresh`` would be
  // recreated every render and the initial-load effect would re-fire
  // after every successful state update.
  const slotsRef = useRef(s)
  slotsRef.current = s
  // Monotonic epoch bumped on every initial-load / refresh /
  // client-change. Both ``refresh`` and ``loadMore`` capture the
  // epoch before awaiting and discard their results if the epoch
  // has advanced, so concurrent or superseded requests cannot commit
  // stale data after ``client`` changes, a refresh, or unmount.
  const requestEpochRef = useRef(0)

  // Advance the epoch on unmount so any in-flight ``client.list()``
  // promise that settles after the component is gone discards its
  // result instead of calling setters against an unmounted tree.
  useEffect(() => {
    return () => {
      requestEpochRef.current += 1
    }
  }, [])

  const refresh = useCallback(async (): Promise<void> => {
    requestEpochRef.current += 1
    const epoch = requestEpochRef.current
    const slots = slotsRef.current
    _resetState(slots)
    try {
      const page = await client.list({ limit: PAGE_SIZE })
      if (epoch !== requestEpochRef.current) return
      _applyPage(slots, page)
    } catch (err) {
      log.warn('list versions failed:', getErrorMessage(err))
      if (epoch === requestEpochRef.current) slots.setError(getErrorMessage(err))
    } finally {
      if (epoch === requestEpochRef.current) slots.setLoading(false)
    }
  }, [client])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const fetchAndAppendPage = useCallback(async (cursor: string, epoch: number): Promise<void> => {
    const slots = slotsRef.current
    try {
      const page = await client.list({ cursor, limit: PAGE_SIZE })
      if (epoch !== requestEpochRef.current) return
      slots.setItems((prev) => [...prev, ...page.data])
      slots.setCursor(page.nextCursor)
      slots.setHasMore(page.hasMore)
      // A previous loadMore() may have populated ``error``; a later
      // successful append must clear it or the stale banner stays
      // visible above newly-loaded rows. Only the request that owns
      // the current epoch may touch the error slot.
      slots.setError(null)
    } catch (err) {
      log.warn('load more versions failed:', getErrorMessage(err))
      if (epoch === requestEpochRef.current) slots.setError(getErrorMessage(err))
    } finally {
      if (epoch === requestEpochRef.current) slots.setLoadingMore(false)
    }
  }, [client])

  const loadMore = useCallback(async (): Promise<void> => {
    const slots = slotsRef.current
    if (slots.loadingMore || slots.loading || !slots.hasMore || slots.cursor === null) return
    slots.setLoadingMore(true)
    await fetchAndAppendPage(slots.cursor, requestEpochRef.current)
  }, [fetchAndAppendPage])

  return {
    items: s.items, cursor: s.cursor, hasMore: s.hasMore,
    loading: s.loading, loadingMore: s.loadingMore, error: s.error,
    selectedVersion: s.selectedVersion, diffOpen: s.diffOpen,
    diffPair: s.diffPair, rollbackOpen: s.rollbackOpen,
    diffFrom: s.diffPair?.from ?? null,
    diffTo: s.diffPair?.to ?? null,
    findById: (id) => s.items.find((i) => String(i.version) === id),
    loadMore,
    refresh,
    select: (item) => _selectVersion(s, item),
    clearSelection: () => s.setSelectedVersion(null),
    openRollback: () => s.setRollbackOpen(true),
    closeRollback: () => s.setRollbackOpen(false),
    closeDiff: () => s.setDiffOpen(false),
  }
}
