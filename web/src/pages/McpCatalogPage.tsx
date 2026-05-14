import { useState } from 'react'
import type { ConnectionType, McpCatalogEntry } from '@/api/types/integrations'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ListHeader } from '@/components/ui/list-header'
import { useConnectionsData } from '@/hooks/useConnectionsData'
import { useMcpCatalogData } from '@/hooks/useMcpCatalogData'
import { useMcpCatalogStore } from '@/stores/mcp-catalog'
import { ConnectionFormModal } from './connections/ConnectionFormModal'
import { ConnectionsSkeleton } from './connections/ConnectionsSkeleton'
import { CatalogDetailDrawer } from './mcp-catalog/CatalogDetailDrawer'
import { CatalogGridView } from './mcp-catalog/CatalogGridView'
import { McpInstallWizard } from './mcp-catalog/McpInstallWizard'
import { McpCatalogSearch } from './mcp-catalog/McpCatalogSearch'

export default function McpCatalogPage() {
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
  const [createConnectionType, setCreateConnectionType] =
    useState<ConnectionType | null>(null)

  const handleInstall = (entry: McpCatalogEntry) => {
    selectEntry(null)
    startInstall(entry.id)
  }

  const emptyTitle = hasSearch ? 'No matching entries' : 'Catalog empty'
  const emptyDescription = hasSearch
    ? `No catalog entries match "${searchQuery}".`
    : 'No MCP servers available in the bundled catalog.'

  return (
    <div className="flex flex-col gap-section-gap">
      <ListHeader
        title="MCP Catalog"
        count={visibleEntries.length}
        primaryAction={<McpCatalogSearch />}
      />

      {error && (
        <ErrorBanner
          severity="error"
          title="Could not load MCP catalog"
          description={error}
        />
      )}

      {(loading || searchLoading) && visibleEntries.length === 0 ? (
        <ConnectionsSkeleton />
      ) : (
        <ErrorBoundary level="section">
          <CatalogGridView
            entries={visibleEntries}
            installedEntryIds={installedEntryIds}
            onSelect={selectEntry}
            onInstall={handleInstall}
            emptyTitle={emptyTitle}
            emptyDescription={emptyDescription}
          />
        </ErrorBoundary>
      )}

      <CatalogDetailDrawer
        entry={selectedEntry}
        installed={
          selectedEntry !== null && installedEntryIds.has(selectedEntry.id)
        }
        onClose={() => selectEntry(null)}
        onInstall={() => {
          if (selectedEntry) handleInstall(selectedEntry)
        }}
        onUninstall={() => {
          if (selectedEntry) void uninstall(selectedEntry.id)
        }}
      />

      <McpInstallWizard
        onRequestCreateConnection={(type) => setCreateConnectionType(type)}
      />

      <ConnectionFormModal
        open={createConnectionType !== null}
        mode="create"
        initialType={createConnectionType ?? undefined}
        onClose={() => setCreateConnectionType(null)}
      />
    </div>
  )
}
