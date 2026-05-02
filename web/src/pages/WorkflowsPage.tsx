import { useCallback, useMemo, useRef, useState } from 'react'
import { AnimatePresence } from 'motion/react'
import { Filter, Plus, Trash2 } from 'lucide-react'
import { useNavigate } from 'react-router'
import { ROUTES } from '@/router/routes'
import { useWorkflowsData } from '@/hooks/useWorkflowsData'
import { useWorkflowsStore } from '@/stores/workflows'
import { Button } from '@/components/ui/button'
import { BulkActionBar } from '@/components/ui/bulk-action-bar'
import { KeyboardShortcutHint } from '@/components/ui/keyboard-shortcut-hint'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { formatNumber } from '@/utils/format'
import { WorkflowsSkeleton } from './workflows/WorkflowsSkeleton'
import { WorkflowFilters } from './workflows/WorkflowFilters'
import { WorkflowGridView } from './workflows/WorkflowGridView'
import { WorkflowTableView } from './workflows/WorkflowTableView'
import { WorkflowCreateDrawer } from './workflows/WorkflowCreateDrawer'

type ViewMode = 'grid' | 'table'

const VIEW_MODE_OPTIONS = [
  { value: 'grid' as const, label: 'Grid' },
  { value: 'table' as const, label: 'Table' },
]

export default function WorkflowsPage() {
  const [createOpen, setCreateOpen] = useState(false)
  const [viewMode, setViewMode] = useState<ViewMode>('grid')
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(() => new Set())
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false)
  const [bulkDeleting, setBulkDeleting] = useState(false)
  const searchInputRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()
  const {
    filteredWorkflows,
    totalWorkflows,
    loading,
    error,
  } = useWorkflowsData()

  const handleDelete = useCallback(async (id: string) => {
    // Store owns success/error UX (toast + surgical rollback on failure).
    // Caller does not need to act on the boolean sentinel; WS updates drive
    // the authoritative list state.
    await useWorkflowsStore.getState().deleteWorkflow(id)
  }, [])

  const handleToggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }, [])

  const clearSelection = useCallback(() => setSelectedIds(new Set()), [])

  const visibleIds = useMemo(
    () => new Set(filteredWorkflows.map((w) => w.id)),
    [filteredWorkflows],
  )
  // Prune selection to workflows that are still visible after filter/refetch.
  const visibleSelected = useMemo(() => {
    const next = new Set<string>()
    for (const id of selectedIds) {
      if (visibleIds.has(id)) next.add(id)
    }
    return next
  }, [selectedIds, visibleIds])
  // `selectedCount` drives the BulkActionBar label and the confirm-dialog
  // copy; anchor it to visibleSelected so the user cannot see
  // "5 selected / Delete 5" when a filter has hidden two of the rows.
  // handleBulkDelete operates on visibleSelected too, so the count always
  // matches the action.
  const selectedCount = visibleSelected.size

  const handleBulkDelete = useCallback(async () => {
    setBulkDeleting(true)
    const ids = [...visibleSelected]
    // Store owns the success/warning/error toast UX (see
    // stores/workflows.ts batchDeleteWorkflows). Caller only drives the
    // dialog and selection state.
    await useWorkflowsStore.getState().batchDeleteWorkflows(ids)
    setBulkDeleting(false)
    setBulkDeleteOpen(false)
    clearSelection()
  }, [visibleSelected, clearSelection])

  const handleDuplicate = useCallback(
    async (id: string) => {
      const workflows = useWorkflowsStore.getState().workflows
      const source = workflows.find((w) => w.id === id)
      if (!source) return
      const created = await useWorkflowsStore.getState().createWorkflow({
        name: `${source.name} (Copy)`,
        description: source.description || undefined,
        workflow_type: source.workflow_type,
        nodes: source.nodes.map((n) => ({ ...n })),
        edges: source.edges.map((e) => ({ ...e })),
      })
      if (!created) return
      navigate(`${ROUTES.WORKFLOW_EDITOR}?id=${encodeURIComponent(created.id)}`)
    },
    [navigate],
  )

  if (loading && totalWorkflows === 0) {
    return <WorkflowsSkeleton />
  }

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Workflows"
        description="Reusable orchestration recipes your agents run."
        count={filteredWorkflows.length}
        countLabel={filteredWorkflows.length === totalWorkflows ? undefined : `${filteredWorkflows.length} of ${totalWorkflows}`}
        primaryAction={
          <div className="flex items-center gap-2">
            <SegmentedControl
              label="View mode"
              value={viewMode}
              onChange={setViewMode}
              options={VIEW_MODE_OPTIONS}
              size="sm"
            />
            <Button size="sm" onClick={() => setCreateOpen(true)}>
              <Plus aria-hidden="true" />
              New workflow
            </Button>
          </div>
        }
      />

      {error && (
        <ErrorBanner severity="error" title="Could not load workflows" description={error} />
      )}

      <WorkflowFilters searchInputRef={searchInputRef} />
      {totalWorkflows > 0 && filteredWorkflows.length === 0 ? (
        <EmptyState
          icon={Filter}
          title="No matching workflows"
          description="Try a different search, loosen the workflow-type filter, or clear everything."
          action={{
            label: 'Clear filters',
            onClick: () => {
              useWorkflowsStore.getState().setSearchQuery('')
              useWorkflowsStore.getState().setWorkflowTypeFilter(null)
              // Re-focus the search input so the user can immediately
              // re-filter without a second click.
              requestAnimationFrame(() => searchInputRef.current?.focus())
            },
          }}
        />
      ) : viewMode === 'grid' ? (
        <WorkflowGridView
          workflows={filteredWorkflows}
          onDelete={handleDelete}
          onDuplicate={handleDuplicate}
          onToggleSelect={handleToggleSelect}
          selectedIds={visibleSelected}
        />
      ) : (
        <WorkflowTableView
          workflows={filteredWorkflows}
          onDelete={handleDelete}
          onDuplicate={handleDuplicate}
          onToggleSelect={handleToggleSelect}
          selectedIds={visibleSelected}
        />
      )}

      <AnimatePresence>
        {selectedCount > 0 && (
          <BulkActionBar
            selectedCount={selectedCount}
            onClear={clearSelection}
            loading={bulkDeleting}
            ariaLabel="Workflow bulk actions"
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
            {/* Surface the clear-shortcut so operators don't have to
                guess that Esc deselects. The hint sits inside the bar
                so it tracks the same dismissal as the action buttons. */}
            <KeyboardShortcutHint
              keys={['Esc']}
              label="to clear"
              className="ml-2"
            />
          </BulkActionBar>
        )}
      </AnimatePresence>

      <ConfirmDialog
        open={bulkDeleteOpen}
        onOpenChange={(open) => { if (!open && !bulkDeleting) setBulkDeleteOpen(false) }}
        title={`Delete ${formatNumber(selectedCount)} workflow${selectedCount === 1 ? '' : 's'}?`}
        description="This will permanently remove every selected workflow definition and its version history. This action cannot be undone."
        confirmLabel={`Delete ${formatNumber(selectedCount)}`}
        variant="destructive"
        loading={bulkDeleting}
        onConfirm={handleBulkDelete}
      />

      <WorkflowCreateDrawer open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  )
}
