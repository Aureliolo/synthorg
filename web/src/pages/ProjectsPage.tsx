import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { FolderKanban, MessagesSquare, Plus } from 'lucide-react'
import { ROUTES } from '@/router/routes'
import { useProjectsData } from '@/hooks/useProjectsData'
import { useProjectsStore } from '@/stores/projects'
import { Button } from '@/components/ui/button'
import { BulkDeleteControls } from '@/components/ui/bulk-delete-controls'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { useBulkSelection } from '@/hooks/use-bulk-selection'
import { useEmptyStateProps } from '@/hooks/use-empty-state-props'
import { useListPagination } from '@/hooks/use-list-pagination'
import { ProjectsSkeleton } from './projects/ProjectsSkeleton'
import { ProjectFilters } from './projects/ProjectFilters'
import { ProjectGridView } from './projects/ProjectGridView'
import { ProjectCreateDrawer } from './projects/ProjectCreateDrawer'

/**
 * Case-appropriate empty state for the project grid: a "New project" CTA when
 * the org genuinely has no projects, or a filter hint when filters narrowed an
 * otherwise-populated list to zero. Returns undefined while the grid has rows.
 */
function useProjectsEmptyNode(
  filteredCount: number,
  totalCount: number,
  onCreate: () => void,
): ReactNode {
  const props = useEmptyStateProps({
    filteredCount,
    totalCount,
    filterActive: totalCount > 0,
    icon: FolderKanban,
    empty: {
      title: 'No projects yet',
      description: 'Create your first project to start organising the org around delivery goals.',
      action: { label: 'New project', onClick: onCreate },
    },
    filtered: {
      title: 'No projects match your filters',
      description: 'Adjust or clear the filters to see more projects.',
    },
  })
  return props !== null ? <EmptyState {...props} /> : undefined
}

function ProjectsHeader({
  filteredCount,
  totalCount,
  onCreate,
}: {
  filteredCount: number
  totalCount: number
  onCreate: () => void
}) {
  return (
    <ListHeader
      title="Projects"
      count={filteredCount}
      countLabel={filteredCount === totalCount ? undefined : `${filteredCount} of ${totalCount}`}
      primaryAction={
        <div className="flex gap-2">
          <Button size="sm" variant="outline" asChild>
            <Link to={`${ROUTES.CHAT}?mode=project`}>
              <MessagesSquare aria-hidden="true" />
              Draft with CEO
            </Link>
          </Button>
          <Button size="sm" onClick={onCreate}>
            <Plus aria-hidden="true" />
            New project
          </Button>
        </div>
      }
    />
  )
}

interface ProjectsBannersProps {
  error: string | null
  wsConnected: boolean
  loading: boolean
  wsSetupError: string | null
}

function ProjectsBanners({ error, wsConnected, loading, wsSetupError }: ProjectsBannersProps) {
  return (
    <>
      {error !== null && (
        <ErrorBanner severity="error" title="Could not load projects" description={error} />
      )}
      {!wsConnected && !loading && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={wsSetupError ?? 'Data may be stale until the connection recovers.'}
        />
      )}
    </>
  )
}

export default function ProjectsPage() {
  const [createOpen, setCreateOpen] = useState(false)
  const {
    filteredProjects,
    totalProjects,
    loading,
    error,
    wsConnected,
    wsSetupError,
  } = useProjectsData()

  const {
    page,
    pageSize,
    totalItems,
    paginatedItems: pagedProjects,
    setPage,
    setPageSize,
  } = useListPagination({ items: filteredProjects, namespace: 'projects' })

  const visibleIds = useMemo(
    () => filteredProjects.map((project) => project.id),
    [filteredProjects],
  )
  const selection = useBulkSelection(
    visibleIds,
    useCallback(
      (ids: readonly string[]) =>
        useProjectsStore.getState().batchDeleteProjects(ids),
      [],
    ),
  )

  const emptyNode = useProjectsEmptyNode(
    filteredProjects.length,
    totalProjects,
    () => setCreateOpen(true),
  )

  if (loading && totalProjects === 0) {
    return <ProjectsSkeleton />
  }

  return (
    <div className="space-y-section-gap">
      <ProjectsHeader
        filteredCount={filteredProjects.length}
        totalCount={totalProjects}
        onCreate={() => setCreateOpen(true)}
      />

      <ProjectsBanners
        error={error}
        wsConnected={wsConnected}
        loading={loading}
        wsSetupError={wsSetupError}
      />

      <ProjectFilters />
      <ProjectGridView
        projects={pagedProjects}
        onToggleSelect={selection.toggle}
        selectedIds={selection.visibleSelected}
        emptyNode={emptyNode}
      />
      <Pagination
        page={page}
        pageSize={pageSize}
        total={totalItems}
        onPageChange={setPage}
        onPageSizeChange={setPageSize}
      />

      <BulkDeleteControls
        selection={selection}
        noun={{ one: 'Project', many: 'projects' }}
        description="This will permanently remove the selected projects, their plans and tasks, and the workspaces their agents wrote into. This action cannot be undone."
        ariaLabel="Project bulk actions"
      />

      <ProjectCreateDrawer open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  )
}
