import { useCallback, useEffect, useState } from 'react'
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

function useBrainList(projectId: string | undefined): {
  entries: readonly BrainSummary[]
  listError: string | null
} {
  const [entries, setEntries] = useState<readonly BrainSummary[]>([])
  const [listError, setListError] = useState<string | null>(null)
  useEffect(() => {
    if (!projectId) return
    const controller = new AbortController()
    listProjectBrain(projectId, undefined, controller.signal)
      .then((result) => {
        setEntries(result.data)
        setListError(null)
      })
      .catch((err: unknown) => {
        if (isAxiosError(err) && err.code === 'ERR_CANCELED') return
        log.warn('list brain failed', err)
        setListError('Could not load the project brain.')
      })
    return () => {
      controller.abort()
    }
  }, [projectId])
  return { entries, listError }
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
}

function useBrainHistory(
  projectId: string | undefined,
  entryId: string | undefined,
): {
  versions: readonly BrainEntryVersion[] | null
  loadHistory: () => void
} {
  const [result, setResult] = useState<HistoryResult | null>(null)
  const loadHistory = useCallback(() => {
    if (!projectId || !entryId) return
    getProjectBrainHistory(projectId, entryId)
      .then((versions) => {
        setResult({ entryId, versions })
      })
      .catch((err: unknown) => {
        log.warn('get brain history failed', err)
        setResult({ entryId, versions: [] })
      })
  }, [projectId, entryId])
  // Derived staleness: history shown only when it matches the current entry,
  // so switching entries resets the panel without a set-state-in-effect.
  const versions =
    result !== null && result.entryId === entryId ? result.versions : null
  return { versions, loadHistory }
}

export default function ProjectBrainPage() {
  const { projectId, entryId } = useParams<{
    projectId: string
    entryId?: string
  }>()
  const navigate = useNavigate()
  const { entries, listError } = useBrainList(projectId)
  const { entry, entryError, entryLoading } = useBrainEntry(projectId, entryId)
  const { versions, loadHistory } = useBrainHistory(projectId, entryId)
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
            selectedEntryId={entryId ?? null}
            kindFilter={kindFilter}
            statusFilter={statusFilter}
            onSelect={handleSelect}
            onKindFilterChange={setKindFilter}
            onStatusFilterChange={setStatusFilter}
          />
          <BrainEntryViewer
            entry={entry}
            loading={entryLoading}
            error={entryError}
            versions={versions}
            onShowHistory={loadHistory}
          />
        </div>
      </ErrorBoundary>
    </div>
  )
}
