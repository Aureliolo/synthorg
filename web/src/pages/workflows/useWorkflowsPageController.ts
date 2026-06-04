import { useCallback, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { ROUTES } from '@/router/routes'
import { useWorkflowsData } from '@/hooks/useWorkflowsData'
import { useWorkflowsStore } from '@/stores/workflows'

export type WorkflowsViewMode = 'grid' | 'table'

export interface WorkflowsPageController {
  data: ReturnType<typeof useWorkflowsData>
  createOpen: boolean
  viewMode: WorkflowsViewMode
  visibleSelected: ReadonlySet<string>
  selectedCount: number
  bulkDeleteOpen: boolean
  bulkDeleting: boolean
  searchInputRef: React.RefObject<HTMLInputElement | null>
  setCreateOpen: (open: boolean) => void
  setViewMode: (mode: WorkflowsViewMode) => void
  setBulkDeleteOpen: (open: boolean) => void
  handleDelete: (id: string) => Promise<boolean>
  handleDuplicate: (id: string) => Promise<void>
  handleExport: (id: string) => Promise<void>
  handleToggleSelect: (id: string) => void
  clearSelection: () => void
  handleBulkDelete: () => Promise<void>
  handleClearFilters: () => void
}

export function useWorkflowsPageController(): WorkflowsPageController {
  const [createOpen, setCreateOpen] = useState(false)
  const [viewMode, setViewMode] = useState<WorkflowsViewMode>('grid')
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(() => new Set())
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false)
  const [bulkDeleting, setBulkDeleting] = useState(false)
  const searchInputRef = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()
  const data = useWorkflowsData()

  const handleDelete = useCallback(
    // Store owns success/error UX. Returning its boolean sentinel lets the
    // ConfirmDialog stay open on failure so the user can retry.
    (id: string) => useWorkflowsStore.getState().deleteWorkflow(id),
    [],
  )

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
    () => new Set(data.filteredWorkflows.map((w) => w.id)),
    [data.filteredWorkflows],
  )
  // Prune selection to workflows that are still visible after filter/refetch.
  const visibleSelected = useMemo(
    () => intersectSets(selectedIds, visibleIds),
    [selectedIds, visibleIds],
  )

  const handleBulkDelete = useCallback(async () => {
    setBulkDeleting(true)
    const ids = [...visibleSelected]
    // Store owns the success/warning/error toast UX.
    await useWorkflowsStore.getState().batchDeleteWorkflows(ids)
    setBulkDeleting(false)
    setBulkDeleteOpen(false)
    clearSelection()
  }, [visibleSelected, clearSelection])

  const handleDuplicate = useCallback(
    async (id: string) => duplicateWorkflow(id, navigate),
    [navigate],
  )

  const handleExport = useCallback(async (id: string) => {
    // Store owns the success/error toast UX for the download.
    await useWorkflowsStore.getState().exportWorkflow(id)
  }, [])

  const handleClearFilters = useCallback(() => {
    useWorkflowsStore.getState().setSearchQuery('')
    useWorkflowsStore.getState().setWorkflowTypeFilter(null)
    requestAnimationFrame(() => searchInputRef.current?.focus())
  }, [])

  return {
    data,
    createOpen,
    viewMode,
    visibleSelected,
    selectedCount: visibleSelected.size,
    bulkDeleteOpen,
    bulkDeleting,
    searchInputRef,
    setCreateOpen,
    setViewMode,
    setBulkDeleteOpen,
    handleDelete,
    handleDuplicate,
    handleExport,
    handleToggleSelect,
    clearSelection,
    handleBulkDelete,
    handleClearFilters,
  }
}

function intersectSets(
  source: ReadonlySet<string>,
  visible: ReadonlySet<string>,
): ReadonlySet<string> {
  const next = new Set<string>()
  for (const id of source) {
    if (visible.has(id)) next.add(id)
  }
  return next
}

async function duplicateWorkflow(
  id: string,
  navigate: (path: string) => unknown,
): Promise<void> {
  const workflows = useWorkflowsStore.getState().workflows
  const source = workflows.find((w) => w.id === id)
  if (!source) return
  const created = await useWorkflowsStore.getState().createWorkflow({
    name: `${source.name} (Copy)`,
    description: source.description ?? '',
    version: '1.0.0',
    workflow_type: source.workflow_type ?? 'sequential_pipeline',
    inputs: [],
    outputs: [],
    is_subworkflow: false,
    nodes: source.nodes.map((n) => ({ ...n })),
    edges: source.edges.map((e) => ({ ...e })),
  })
  if (!created) return
  void navigate(`${ROUTES.WORKFLOW_EDITOR}?id=${encodeURIComponent(created.id)}`)
}
