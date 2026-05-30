import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import {
  getProjectBrainEntry,
  getProjectBrainHistory,
  listProjectBrain,
} from '@/api/endpoints/projectBrain'
import type {
  BrainEntry,
  BrainEntryKind,
  BrainEntryStatus,
  BrainEntryVersion,
  BrainSummary,
} from '@/api/types'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { createLogger } from '@/lib/logger'
import { ROUTES } from '@/router/routes'
import { isAxiosError } from '@/utils/errors'
import { BrainEntryList } from './project-brain/BrainEntryList'
import { BrainEntryViewer } from './project-brain/BrainEntryViewer'

const log = createLogger('project-brain-page')

interface EntryFetchResult {
  projectId: string
  entryId: string
  entry: BrainEntry | null
  error: string | null
}

interface ResolvedEntry {
  entry: BrainEntry | null
  entryError: string | null
  entryLoading: boolean
}

function resolveEntry(
  projectId: string | undefined,
  entryId: string | undefined,
  result: EntryFetchResult | null,
): ResolvedEntry {
  if (!projectId || !entryId) {
    return { entry: null, entryError: null, entryLoading: false }
  }
  if (
    result === null ||
    result.projectId !== projectId ||
    result.entryId !== entryId
  ) {
    return { entry: null, entryError: null, entryLoading: true }
  }
  return { entry: result.entry, entryError: result.error, entryLoading: false }
}

interface BrainListState {
  entries: readonly BrainSummary[]
  listError: string | null
  listLoading: boolean
  hasMore: boolean
  loadMore: () => void
}

function useBrainList(projectId: string | undefined): BrainListState {
  const [entries, setEntries] = useState<readonly BrainSummary[]>([])
  const [listError, setListError] = useState<string | null>(null)
  const [listLoading, setListLoading] = useState(false)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (!projectId) return
    const controller = new AbortController()
    controllerRef.current = controller
    const fetchList = async () => {
      setListLoading(true)
      setHasMore(false)
      try {
        const result = await listProjectBrain(projectId, undefined, controller.signal)
        setEntries(result.data)
        setNextCursor(result.nextCursor)
        setHasMore(result.hasMore)
        setListError(null)
      } catch (err: unknown) {
        if (isAxiosError(err) && err.code === 'ERR_CANCELED') return
        log.warn('list brain failed', err)
        setListError('Could not load the project brain.')
      } finally {
        if (!controller.signal.aborted) setListLoading(false)
      }
    }
    void fetchList()
    return () => {
      // Abort whichever request is in flight (this load or a loadMore that
      // reassigned the ref), not just this effect's controller.
      controllerRef.current?.abort()
    }
  }, [projectId])

  const loadMore = useCallback(() => {
    if (!projectId || !hasMore || nextCursor === null) return
    // Each page request owns a fresh controller (mirroring loadHistory) so it
    // never rides the list effect's controller, which may already be aborted.
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setListLoading(true)
    listProjectBrain(projectId, { cursor: nextCursor }, controller.signal)
      .then((result) => {
        setEntries((prev) => [...prev, ...result.data])
        setNextCursor(result.nextCursor)
        setHasMore(result.hasMore)
        setListLoading(false)
      })
      .catch((err: unknown) => {
        if (isAxiosError(err) && err.code === 'ERR_CANCELED') return
        log.warn('load more brain failed', err)
        setListError('Could not load more brain entries.')
        setListLoading(false)
      })
  }, [projectId, hasMore, nextCursor])

  return { entries, listError, listLoading, hasMore, loadMore }
}

function useBrainEntry(
  projectId: string | undefined,
  entryId: string | undefined,
): ResolvedEntry {
  const [result, setResult] = useState<EntryFetchResult | null>(null)
  useEffect(() => {
    if (!projectId || !entryId) return
    const controller = new AbortController()
    getProjectBrainEntry(projectId, entryId, controller.signal)
      .then((entry) => {
        setResult({ projectId, entryId, entry, error: null })
      })
      .catch((err: unknown) => {
        if (isAxiosError(err) && err.code === 'ERR_CANCELED') return
        log.warn('get brain entry failed', err)
        setResult({
          projectId,
          entryId,
          entry: null,
          error: 'Could not load this entry.',
        })
      })
    return () => {
      controller.abort()
    }
  }, [projectId, entryId])
  return resolveEntry(projectId, entryId, result)
}

interface HistoryResult {
  entryId: string
  versions: readonly BrainEntryVersion[]
  error: string | null
}

interface BrainHistoryState {
  versions: readonly BrainEntryVersion[] | null
  historyError: string | null
  loadHistory: () => void
}

function useBrainHistory(
  projectId: string | undefined,
  entryId: string | undefined,
): BrainHistoryState {
  const [result, setResult] = useState<HistoryResult | null>(null)
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(
    () => () => {
      controllerRef.current?.abort()
    },
    [],
  )

  const loadHistory = useCallback(() => {
    if (!projectId || !entryId) return
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    getProjectBrainHistory(projectId, entryId, controller.signal)
      .then((versions) => {
        setResult({ entryId, versions, error: null })
      })
      .catch((err: unknown) => {
        if (isAxiosError(err) && err.code === 'ERR_CANCELED') return
        log.warn('get brain history failed', err)
        setResult({
          entryId,
          versions: [],
          error: 'Could not load revision history.',
        })
      })
  }, [projectId, entryId])
  // Derived staleness: history shown only when it matches the current entry,
  // so switching entries resets the panel without a set-state-in-effect.
  const matches = result !== null && result.entryId === entryId
  const versions = matches ? result.versions : null
  const historyError = matches ? result.error : null
  return { versions, historyError, loadHistory }
}

function MissingProjectBanner() {
  return (
    <div className="space-y-section-gap">
      <Breadcrumbs items={[{ label: 'Projects', to: ROUTES.PROJECTS }]} />
      <ErrorBanner
        severity="error"
        title="Missing project"
        description="No project identifier in the URL."
      />
    </div>
  )
}

export default function ProjectBrainPage() {
  const { projectId, entryId } = useParams<{
    projectId: string
    entryId?: string
  }>()
  const navigate = useNavigate()
  const { entries, listError, listLoading, hasMore, loadMore } =
    useBrainList(projectId)
  const { entry, entryError, entryLoading } = useBrainEntry(projectId, entryId)
  const { versions, historyError, loadHistory } = useBrainHistory(
    projectId,
    entryId,
  )
  const [kindFilter, setKindFilter] = useState<BrainEntryKind | null>(null)
  const [statusFilter, setStatusFilter] = useState<BrainEntryStatus | null>(null)

  const handleSelect = useCallback(
    (selectedEntryId: string) => {
      if (!projectId) return
      void navigate(
        ROUTES.PROJECT_BRAIN_DETAIL.replace(
          ':projectId',
          encodeURIComponent(projectId),
        ).replace(':entryId', encodeURIComponent(selectedEntryId)),
      )
    },
    [navigate, projectId],
  )

  if (!projectId) {
    return <MissingProjectBanner />
  }

  const projectDetailPath = ROUTES.PROJECT_DETAIL.replace(
    ':projectId',
    encodeURIComponent(projectId),
  )

  return (
    <div className="space-y-section-gap">
      <Breadcrumbs
        items={[
          { label: 'Projects', to: ROUTES.PROJECTS },
          { label: projectId, to: projectDetailPath },
          { label: 'Brain' },
        ]}
      />
      {listError !== null && (
        <ErrorBanner
          severity="error"
          title="Could not load brain"
          description={listError}
        />
      )}
      <ErrorBoundary level="section">
        <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-[320px_1fr]">
          <BrainEntryList
            entries={entries}
            loading={listLoading}
            hasMore={hasMore}
            selectedEntryId={entryId ?? null}
            kindFilter={kindFilter}
            statusFilter={statusFilter}
            onSelect={handleSelect}
            onLoadMore={loadMore}
            onKindFilterChange={setKindFilter}
            onStatusFilterChange={setStatusFilter}
          />
          <BrainEntryViewer
            entry={entry}
            loading={entryLoading}
            error={entryError}
            versions={versions}
            historyError={historyError}
            onShowHistory={loadHistory}
          />
        </div>
      </ErrorBoundary>
    </div>
  )
}
