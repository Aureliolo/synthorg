import { AnimatePresence } from 'motion/react'
import { Filter, Plus, Trash2 } from 'lucide-react'
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
import {
  useWorkflowsPageController,
  type WorkflowsPageController,
} from './workflows/useWorkflowsPageController'

const VIEW_MODE_OPTIONS = [
  { value: 'grid' as const, label: 'Grid' },
  { value: 'table' as const, label: 'Table' },
]

export default function WorkflowsPage() {
  const ctrl = useWorkflowsPageController()

  if (ctrl.data.loading && ctrl.data.totalWorkflows === 0) {
    return <WorkflowsSkeleton />
  }

  const countLabel =
    ctrl.data.filteredWorkflows.length === ctrl.data.totalWorkflows
      ? undefined
      : `${ctrl.data.filteredWorkflows.length} of ${ctrl.data.totalWorkflows}`

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Workflows"
        description="Reusable orchestration recipes your agents run."
        count={ctrl.data.filteredWorkflows.length}
        countLabel={countLabel}
        primaryAction={
          <WorkflowsHeaderActions
            viewMode={ctrl.viewMode}
            onViewModeChange={ctrl.setViewMode}
            onCreateClick={() => ctrl.setCreateOpen(true)}
          />
        }
      />

      {ctrl.data.error && (
        <ErrorBanner
          severity="error"
          title="Could not load workflows"
          description={ctrl.data.error}
        />
      )}

      <WorkflowFilters searchInputRef={ctrl.searchInputRef} />
      <WorkflowsListBody ctrl={ctrl} />
      <WorkflowsBulkActions ctrl={ctrl} />
      <ConfirmDialog
        open={ctrl.bulkDeleteOpen}
        onOpenChange={(open) => {
          if (!open && !ctrl.bulkDeleting) ctrl.setBulkDeleteOpen(false)
        }}
        title={`Delete ${formatNumber(ctrl.selectedCount)} workflow${
          ctrl.selectedCount === 1 ? '' : 's'
        }?`}
        description="This will permanently remove every selected workflow definition and its version history. This action cannot be undone."
        confirmLabel={`Delete ${formatNumber(ctrl.selectedCount)}`}
        variant="destructive"
        loading={ctrl.bulkDeleting}
        onConfirm={ctrl.handleBulkDelete}
      />
      <WorkflowCreateDrawer
        open={ctrl.createOpen}
        onClose={() => ctrl.setCreateOpen(false)}
      />
    </div>
  )
}

interface WorkflowsHeaderActionsProps {
  viewMode: WorkflowsPageController['viewMode']
  onViewModeChange: WorkflowsPageController['setViewMode']
  onCreateClick: () => void
}

function WorkflowsHeaderActions({
  viewMode,
  onViewModeChange,
  onCreateClick,
}: WorkflowsHeaderActionsProps) {
  return (
    <div className="flex items-center gap-2">
      <SegmentedControl
        label="View mode"
        value={viewMode}
        onChange={onViewModeChange}
        options={VIEW_MODE_OPTIONS}
        size="sm"
      />
      <Button size="sm" onClick={onCreateClick}>
        <Plus aria-hidden="true" />
        New workflow
      </Button>
    </div>
  )
}

interface WorkflowsListBodyProps {
  ctrl: WorkflowsPageController
}

function WorkflowsListBody({ ctrl }: WorkflowsListBodyProps) {
  const hasFilterMismatch =
    ctrl.data.totalWorkflows > 0 && ctrl.data.filteredWorkflows.length === 0
  if (hasFilterMismatch) {
    return (
      <EmptyState
        icon={Filter}
        title="No matching workflows"
        description="Try a different search, loosen the workflow-type filter, or clear everything."
        action={{ label: 'Clear filters', onClick: ctrl.handleClearFilters }}
      />
    )
  }
  if (ctrl.viewMode === 'grid') {
    return (
      <WorkflowGridView
        workflows={ctrl.data.filteredWorkflows}
        onDelete={ctrl.handleDelete}
        onDuplicate={ctrl.handleDuplicate}
        onExport={ctrl.handleExport}
        onToggleSelect={ctrl.handleToggleSelect}
        selectedIds={ctrl.visibleSelected}
      />
    )
  }
  return (
    <WorkflowTableView
      workflows={ctrl.data.filteredWorkflows}
      onDelete={ctrl.handleDelete}
      onDuplicate={ctrl.handleDuplicate}
      onExport={ctrl.handleExport}
      onToggleSelect={ctrl.handleToggleSelect}
      selectedIds={ctrl.visibleSelected}
    />
  )
}

interface WorkflowsBulkActionsProps {
  ctrl: WorkflowsPageController
}

function WorkflowsBulkActions({ ctrl }: WorkflowsBulkActionsProps) {
  return (
    <AnimatePresence>
      {ctrl.selectedCount > 0 && (
        <BulkActionBar
          selectedCount={ctrl.selectedCount}
          onClear={ctrl.clearSelection}
          loading={ctrl.bulkDeleting}
          ariaLabel="Workflow bulk actions"
        >
          <Button
            size="sm"
            variant="outline"
            className="gap-1 border-danger/30 text-danger hover:bg-danger/10"
            onClick={() => ctrl.setBulkDeleteOpen(true)}
            disabled={ctrl.bulkDeleting}
          >
            <Trash2 className="size-3.5" />
            Delete {formatNumber(ctrl.selectedCount)}
          </Button>
          <KeyboardShortcutHint keys={['Esc']} label="to clear" className="ml-2" />
        </BulkActionBar>
      )}
    </AnimatePresence>
  )
}
