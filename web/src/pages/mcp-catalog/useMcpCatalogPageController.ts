import { useState } from 'react'
import type { ConnectionType, McpCatalogEntry } from '@/api/types/integrations'
import { useConnectionsData } from '@/hooks/useConnectionsData'
import { useMcpCatalogData } from '@/hooks/useMcpCatalogData'
import { useMcpCatalogStore } from '@/stores/mcp-catalog'

export interface McpCatalogPageController {
  visibleEntries: ReturnType<typeof useMcpCatalogData>['visibleEntries']
  error: string | null
  showSkeleton: boolean
  emptyTitle: string
  emptyDescription: string
  installedEntryIds: ReadonlySet<string>
  selectedEntry: McpCatalogEntry | null
  selectedEntryInstalled: boolean
  createConnectionType: ConnectionType | null
  handleSelect: (entry: McpCatalogEntry | null) => void
  handleInstall: (entry: McpCatalogEntry) => void
  handleSelectedInstall: () => void
  handleSelectedUninstall: () => void
  closeSelected: () => void
  setCreateConnectionType: (type: ConnectionType | null) => void
}

export function useMcpCatalogPageController(): McpCatalogPageController {
  const {
    visibleEntries,
    loading,
    searchLoading,
    searchQuery,
    hasSearch,
    error,
  } = useMcpCatalogData()
  // Keep connections warm so the install wizard can offer them.
  useConnectionsData()
  const installedEntryIds = useMcpCatalogStore((s) => s.installedEntryIds)
  const selectedEntry = useMcpCatalogStore((s) => s.selectedEntry)
  const selectEntry = useMcpCatalogStore((s) => s.selectEntry)
  const startInstall = useMcpCatalogStore((s) => s.startInstall)
  const uninstall = useMcpCatalogStore((s) => s.uninstall)
  const [createConnectionType, setCreateConnectionType] = useState<ConnectionType | null>(
    null,
  )

  const handleInstall = (entry: McpCatalogEntry) => {
    selectEntry(null)
    startInstall(entry.id)
  }

  return {
    visibleEntries,
    error,
    showSkeleton: (loading || searchLoading) && visibleEntries.length === 0,
    emptyTitle: hasSearch ? 'No matching entries' : 'Catalog empty',
    emptyDescription: hasSearch
      ? `No catalog entries match "${searchQuery}".`
      : 'No MCP servers available in the bundled catalog.',
    installedEntryIds,
    selectedEntry,
    selectedEntryInstalled:
      selectedEntry !== null && installedEntryIds.has(selectedEntry.id),
    createConnectionType,
    handleSelect: selectEntry,
    handleInstall,
    handleSelectedInstall: () => {
      if (selectedEntry) handleInstall(selectedEntry)
    },
    handleSelectedUninstall: () => {
      if (selectedEntry) void uninstall(selectedEntry.id)
    },
    closeSelected: () => selectEntry(null),
    setCreateConnectionType,
  }
}
