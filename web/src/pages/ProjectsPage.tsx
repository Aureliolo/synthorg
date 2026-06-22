import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { AnimatePresence } from 'motion/react'
import { FolderKanban, Plus, Trash2 } from 'lucide-react'
import { useProjectsData } from '@/hooks/useProjectsData'
import { useProjectsStore } from '@/stores/projects'
import { Button } from '@/components/ui/button'
import { BulkActionBar } from '@/components/ui/bulk-action-bar'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { useEmptyStateProps } from '@/hooks/use-empty-state-props'
import { useListPagination } from '@/hooks/use-list-pagination'
import { formatNumber } from '@/utils/format'
import { ProjectsSkeleton } from './projects/ProjectsSkeleton'
import { ProjectFilters } from './projects/ProjectFilters'
import { ProjectGridView } from './projects/ProjectGridView'
import { ProjectCreateDrawer } from './projects/ProjectCreateDrawer'

type ProjectList = ReturnType<typeof useProjectsData>['filteredProjects']

interface ProjectSelection {
  visibleSelected: ReadonlySet<string>
  selectedCount: number
  handleToggleSelect: (id: string) => void
  clearSelection: () => void
  bulkDeleteOpen: boolean
  setBulkDeleteOpen: (open: boolean) => void
  bulkDeleting: boolean
  handleBulkDelete: () => Promise<void>
}

function useProjectSelection(filteredProjects: ProjectList): ProjectSelection {
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(() => new Set())
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false)
  const [bulkDeleting, setBulkDeleting] = useState(false)

  const handleToggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const clearSelection = useCallback(() => setSelectedIds(new Set()), [])

  const visibleIds = useMemo(
    () => new Set(filteredProjects.map((p) => p.id)),
    [filteredProjects],
  )
  const visibleSelected = useMemo(() => {
    const next = new Set<string>()
    for (const id of selectedIds) {
      if (visibleIds.has(id)) next.add(id)
    }
    return next
  }, [selectedIds, visibleIds])

  const handleBulkDelete = useCallback(async () => {
    setBulkDeleting(true)
    const ids = [...visibleSelected]
    // Store owns the success / warning / error toast UX (see
    // stores/projects.ts batchDeleteProjects). The caller only drives
    // the dialog and selection state, so we discard the returned
    // counts/sentinel here.
    await useProjectsStore.getState().batchDeleteProjects(ids)
    setBulkDeleting(false)
    setBulkDeleteOpen(false)
    clearSelection()
  }, [visibleSelected, clearSelection])

  return {
    visibleSelected,
    selectedCount: visibleSelected.size,
    handleToggleSelect,
    clearSelection,
    bulkDeleteOpen,
    setBulkDeleteOpen,
    bulkDeleting,
    handleBulkDelete,
  }
}

function ProjectsBulkActions({
  selectedCount,
  clearSelection,
  bulkDeleting,
  bulkDeleteOpen,
  setBulkDeleteOpen,
  onConfirm,
}: {
  selectedCount: number
  clearSelection: () => void
  bulkDeleting: boolean
  bulkDeleteOpen: boolean
  setBulkDeleteOpen: (open: boolean) => void
  onConfirm: () => Promise<void>
}) {
  return (
    <>
      <AnimatePresence>
        {selectedCount > 0 && (
          <BulkActionBar
            selectedCount={selectedCount}
            onClear={clearSelection}
            loading={bulkDeleting}
            ariaLabel="Project bulk actions"
          >
            <Button
              size="sm"
              variant="outline"
              className="gap-1 border-danger/30 text-danger hover:bg-danger/10"
              onClick={() => setBulkDeleteOpen(true)}
              disabled={bulkDeleting}
            >
              <Trash2 className="size-3.5" />
              Delete {formatNumber(selectedCount)}
            </Button>
          </BulkActionBar>
        )}
      </AnimatePresence>

      <ConfirmDialog
        open={bulkDeleteOpen}
        onOpenChange={(open) => { if (!open && !bulkDeleting) setBulkDeleteOpen(false) }}
        title={`Delete ${formatNumber(selectedCount)} project${selectedCount === 1 ? '' : 's'}?`}
        description="This will permanently remove the selected projects. Associated tasks remain, but their project link will be broken. This action cannot be undone."
        confirmLabel={`Delete ${formatNumber(selectedCount)}`}
        variant="destructive"
        loading={bulkDeleting}
        onConfirm={onConfirm}
      />
    </>
  )
}

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
        <Button size="sm" onClick={onCreate}>
          <Plus aria-hidden="true" />
          New project
        </Button>
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

  const {
    visibleSelected,
    selectedCount,
    handleToggleSelect,
    clearSelection,
    bulkDeleteOpen,
    setBulkDeleteOpen,
    bulkDeleting,
    handleBulkDelete,
  } = useProjectSelection(filteredProjects)

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
        onToggleSelect={handleToggleSelect}
        selectedIds={visibleSelected}
        emptyNode={emptyNode}
      />
      <Pagination
        page={page}
        pageSize={pageSize}
        total={totalItems}
        onPageChange={setPage}
        onPageSizeChange={setPageSize}
      />

      <ProjectsBulkActions
        selectedCount={selectedCount}
        clearSelection={clearSelection}
        bulkDeleting={bulkDeleting}
        bulkDeleteOpen={bulkDeleteOpen}
        setBulkDeleteOpen={setBulkDeleteOpen}
        onConfirm={handleBulkDelete}
      />

      <ProjectCreateDrawer open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  )
}
