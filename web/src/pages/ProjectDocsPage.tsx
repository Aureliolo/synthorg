import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { getProjectDoc, listProjectDocs } from '@/api/endpoints/projectDocs'
import type { DocSummary, DocType, LivingDocument } from '@/api/types'
import { isAxiosError } from '@/utils/errors'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { createLogger } from '@/lib/logger'
import { ROUTES } from '@/router/routes'
import { DocList } from './project-docs/DocList'
import { DocViewer } from './project-docs/DocViewer'

const log = createLogger('project-docs-page')

interface DocFetchResult {
  projectId: string
  slug: string
  doc: LivingDocument | null
  error: string | null
}

interface ResolvedDoc {
  doc: LivingDocument | null
  docError: string | null
  docLoading: boolean
}

// A result is only current when it matches the requested project AND
// slug; a stale result from a previous project/slug reads as "still
// loading". Keying on slug alone briefly surfaced project A's doc for
// project B when both expose a doc under the same slug.
function resolveDoc(
  projectId: string | undefined,
  slug: string | undefined,
  docResult: DocFetchResult | null,
): ResolvedDoc {
  if (!projectId || !slug) {
    return { doc: null, docError: null, docLoading: false }
  }
  if (docResult === null || docResult.projectId !== projectId || docResult.slug !== slug) {
    return { doc: null, docError: null, docLoading: true }
  }
  return { doc: docResult.doc, docError: docResult.error, docLoading: false }
}

interface ProjectDocsData {
  docs: readonly DocSummary[]
  listError: string | null
  filter: DocType | null
  setFilter: (filter: DocType | null) => void
  doc: LivingDocument | null
  docError: string | null
  docLoading: boolean
  handleSelect: (selectedSlug: string) => void
}

function useProjectDocsData(
  projectId: string | undefined,
  slug: string | undefined,
): ProjectDocsData {
  const navigate = useNavigate()
  const [docs, setDocs] = useState<readonly DocSummary[]>([])
  const [docResult, setDocResult] = useState<DocFetchResult | null>(null)
  const [filter, setFilter] = useState<DocType | null>(null)
  const [listError, setListError] = useState<string | null>(null)

  useEffect(() => {
    if (!projectId) return
    const controller = new AbortController()
    listProjectDocs(projectId, undefined, controller.signal)
      .then((result) => {
        setDocs(result.data)
        setListError(null)
      })
      .catch((err: unknown) => {
        if (isAxiosError(err) && err.code === 'ERR_CANCELED') return
        log.warn('list docs failed', err)
        setListError('Could not load documents for this project.')
      })
    return () => {
      controller.abort()
    }
  }, [projectId])

  useEffect(() => {
    if (!projectId || !slug) return
    const controller = new AbortController()
    getProjectDoc(projectId, slug, controller.signal)
      .then((value) => {
        setDocResult({ projectId, slug, doc: value, error: null })
      })
      .catch((err: unknown) => {
        if (isAxiosError(err) && err.code === 'ERR_CANCELED') return
        log.warn('get doc failed', err)
        setDocResult({ projectId, slug, doc: null, error: 'Could not load this document.' })
      })
    return () => {
      controller.abort()
    }
  }, [projectId, slug])

  const { doc, docError, docLoading } = resolveDoc(projectId, slug, docResult)

  const handleSelect = useCallback(
    (selectedSlug: string) => {
      if (!projectId) return
      const target = ROUTES.PROJECT_DOC_DETAIL.replace(
        ':projectId',
        encodeURIComponent(projectId),
      ).replace(':slug', encodeURIComponent(selectedSlug))
      void navigate(target)
    },
    [navigate, projectId],
  )

  return { docs, listError, filter, setFilter, doc, docError, docLoading, handleSelect }
}

export default function ProjectDocsPage() {
  const { projectId, slug } = useParams<{ projectId: string; slug?: string }>()
  const { docs, listError, filter, setFilter, doc, docError, docLoading, handleSelect } =
    useProjectDocsData(projectId, slug)

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
          { label: 'Docs' },
        ]}
      />
      {listError !== null && (
        <ErrorBanner
          severity="error"
          title="Could not load docs"
          description={listError}
        />
      )}
      <ErrorBoundary level="section">
        <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-[280px_1fr]">
          <DocList
            docs={docs}
            selectedSlug={slug ?? null}
            filter={filter}
            onSelect={handleSelect}
            onFilterChange={setFilter}
          />
          <DocViewer
            doc={doc}
            loading={docLoading}
            error={docError}
            projectId={projectId}
          />
        </div>
      </ErrorBoundary>
    </div>
  )
}
