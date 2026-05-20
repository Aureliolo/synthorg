import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import {
  getProjectDoc,
  listProjectDocs,
  type DocSummary,
  type DocType,
  type LivingDocument,
} from '@/api/endpoints/projectDocs'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { createLogger } from '@/lib/logger'
import { ROUTES } from '@/router/routes'
import { DocList } from './project-docs/DocList'
import { DocViewer } from './project-docs/DocViewer'

const log = createLogger('project-docs-page')

export default function ProjectDocsPage() {
  const { projectId, slug } = useParams<{ projectId: string; slug?: string }>()
  const navigate = useNavigate()
  const [docs, setDocs] = useState<readonly DocSummary[]>([])
  const [doc, setDoc] = useState<LivingDocument | null>(null)
  const [filter, setFilter] = useState<DocType | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [docError, setDocError] = useState<string | null>(null)
  const [docLoading, setDocLoading] = useState<boolean>(false)

  useEffect(() => {
    if (!projectId) return
    let cancelled = false
    listProjectDocs(projectId)
      .then((result) => {
        if (cancelled) return
        setDocs(result.data)
        setListError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        log.warn('list docs failed', err)
        setListError('Could not load documents for this project.')
      })
    return () => {
      cancelled = true
    }
  }, [projectId])

  useEffect(() => {
    if (!projectId || !slug) return
    let cancelled = false
    getProjectDoc(projectId, slug)
      .then((value) => {
        if (cancelled) return
        setDoc(value)
        setDocError(null)
        setDocLoading(false)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        log.warn('get doc failed', err)
        setDocError('Could not load this document.')
        setDocLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [projectId, slug])


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
            doc={slug ? doc : null}
            loading={Boolean(slug) && docLoading}
            error={slug ? docError : null}
          />
        </div>
      </ErrorBoundary>
    </div>
  )
}
