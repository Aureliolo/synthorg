import { useCallback } from 'react'
import { useParams } from 'react-router'
import { useProjectDetailData } from '@/hooks/useProjectDetailData'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { DetailNavBar } from '@/components/ui/detail-nav-bar'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ROUTES } from '@/router/routes'
import {
  useDetailNavigation,
  useDetailNavigationCallbacks,
} from '@/hooks/use-detail-navigation'
import { useProjectsStore } from '@/stores/projects'
import { ProjectDetailSkeleton } from './projects/ProjectDetailSkeleton'
import { ProjectHeader } from './projects/ProjectHeader'
import { ProjectTeamSection } from './projects/ProjectTeamSection'
import { ProjectTaskList } from './projects/ProjectTaskList'

export default function ProjectDetailPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const {
    project,
    projectTasks,
    loading,
    error,
    wsConnected,
    wsSetupError,
  } = useProjectDetailData(projectId ?? '')

  // Walk the parent list so prev/next preserves the operator's
  // filter/sort context. Empty on a deep link; the nav bar self-hides.
  const allProjects = useProjectsStore((s) => s.projects)
  const routeForProject = useCallback(
    (item: { id: string }) =>
      ROUTES.PROJECT_DETAIL.replace(':projectId', encodeURIComponent(item.id)),
    [],
  )
  const nav = useDetailNavigation({
    items: allProjects,
    currentId: projectId,
    routeFor: routeForProject,
  })
  const { goPrev, goNext } = useDetailNavigationCallbacks(nav)

  // Error state: a definitive negative answer from the backend always
  // sets ``error`` -- show the not-found banner only when the fetch
  // failed, never on the pre-fetch render window where ``loading``
  // hasn't flipped to ``true`` yet (the polling effect runs after
  // first paint).
  if (error && !project) {
    return (
      <div className="space-y-section-gap">
        <Breadcrumbs items={[{ label: 'Projects', to: ROUTES.PROJECTS }, { label: 'Unknown project' }]} />
        <ErrorBanner severity="error" title="Project not found" description={error} />
      </div>
    )
  }

  if (!project) {
    return <ProjectDetailSkeleton />
  }

  return (
    <div className="space-y-section-gap">
      <div className="flex flex-wrap items-center gap-3">
        <Breadcrumbs items={[{ label: 'Projects', to: ROUTES.PROJECTS }, { label: project.name }]} />
        <DetailNavBar
          canPrev={nav.canPrev}
          canNext={nav.canNext}
          onPrev={goPrev}
          onNext={goNext}
          position={nav.position}
        />
      </div>

      {error && (
        <ErrorBanner severity="error" title="Could not load project" description={error} />
      )}

      {!wsConnected && !loading && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={wsSetupError ?? 'Data may be stale until the connection recovers.'}
        />
      )}

      <ErrorBoundary level="section">
        <ProjectHeader project={project} />
      </ErrorBoundary>

      <div className="grid grid-cols-2 gap-grid-gap max-[1023px]:grid-cols-1">
        <ErrorBoundary level="section">
          <ProjectTeamSection project={project} />
        </ErrorBoundary>

        <ErrorBoundary level="section">
          <ProjectTaskList tasks={projectTasks} />
        </ErrorBoundary>
      </div>
    </div>
  )
}
