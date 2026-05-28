import { useCallback, useMemo, useState } from 'react'

import { useAgentsData } from '@/hooks/useAgentsData'
import { useListPagination } from '@/hooks/use-list-pagination'
import { createLogger } from '@/lib/logger'
import { useCompanyStore } from '@/stores/company'
import { useToastStore } from '@/stores/toast'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('AgentsPage')

export interface AgentsPageController {
  data: ReturnType<typeof useAgentsData>
  visibleSelected: ReadonlySet<string>
  selectedCount: number
  bulkDeleteOpen: boolean
  bulkDeleting: boolean
  pagination: ReturnType<typeof useListPagination<ReturnType<typeof useAgentsData>['filteredAgents'][number]>>
  setBulkDeleteOpen: (open: boolean) => void
  handleToggleSelect: (id: string) => void
  clearSelection: () => void
  handleBulkDelete: () => Promise<void>
}

export function useAgentsPageController(): AgentsPageController {
  const data = useAgentsData()
  const deleteAgent = useCompanyStore((s) => s.deleteAgent)
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
    () => new Set(data.filteredAgents.map((a) => a.id ?? a.name)),
    [data.filteredAgents],
  )
  const visibleSelected = useMemo(
    () => intersectSets(selectedIds, visibleIds),
    [selectedIds, visibleIds],
  )

  const idToName = useMemo(() => {
    const m = new Map<string, string>()
    for (const a of data.filteredAgents) m.set(a.id ?? a.name, a.name)
    return m
  }, [data.filteredAgents])

  const handleBulkDelete = useCallback(async () => {
    setBulkDeleting(true)
    const { succeeded, failed } = await runBulkDelete(visibleSelected, idToName, deleteAgent)
    setBulkDeleting(false)
    setBulkDeleteOpen(false)
    clearSelection()
    emitBulkDeleteToast(succeeded, failed)
    if (failed > 0 && succeeded === 0) {
      log.warn('bulk_agent_delete_all_failed', sanitizeForLog({ failed }))
    }
  }, [visibleSelected, idToName, deleteAgent, clearSelection])

  const pagination = useListPagination({
    items: data.filteredAgents,
    namespace: 'agents',
  })

  return {
    data,
    visibleSelected,
    selectedCount: visibleSelected.size,
    bulkDeleteOpen,
    bulkDeleting,
    pagination,
    setBulkDeleteOpen,
    handleToggleSelect,
    clearSelection,
    handleBulkDelete,
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

async function runBulkDelete(
  visibleSelected: ReadonlySet<string>,
  idToName: Map<string, string>,
  deleteAgent: ReturnType<typeof useCompanyStore.getState>['deleteAgent'],
): Promise<{ succeeded: number; failed: number }> {
  let succeeded = 0
  let failed = 0
  for (const id of visibleSelected) {
    const name = idToName.get(id)
    if (!name) continue
    const ok = await deleteAgent(name)
    if (ok) succeeded += 1
    else failed += 1
  }
  return { succeeded, failed }
}

function emitBulkDeleteToast(succeeded: number, failed: number): void {
  if (succeeded === 0) return
  useToastStore.getState().add({
    variant: failed === 0 ? 'success' : 'warning',
    title:
      failed === 0
        ? `${succeeded} agent${succeeded === 1 ? '' : 's'} deleted`
        : `${succeeded} deleted; ${failed} failed`,
  })
}
