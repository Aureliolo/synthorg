import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ListHeader } from '@/components/ui/list-header'
import { ConnectionFormModal } from './connections/ConnectionFormModal'
import { ConnectionsSkeleton } from './connections/ConnectionsSkeleton'
import { CatalogDetailDrawer } from './mcp-catalog/CatalogDetailDrawer'
import { CatalogGridView } from './mcp-catalog/CatalogGridView'
import { McpInstallWizard } from './mcp-catalog/McpInstallWizard'
import { McpCatalogSearch } from './mcp-catalog/McpCatalogSearch'
import { useMcpCatalogPageController } from './mcp-catalog/useMcpCatalogPageController'

export default function McpCatalogPage() {
  const ctrl = useMcpCatalogPageController()

  return (
    <div className="flex flex-col gap-section-gap">
      <ListHeader
        title="MCP Catalog"
        count={ctrl.visibleEntries.length}
        primaryAction={<McpCatalogSearch />}
      />

      {ctrl.error && (
        <ErrorBanner
          severity="error"
          title="Could not load MCP catalog"
          description={ctrl.error}
        />
      )}

      {ctrl.showSkeleton ? (
        <ConnectionsSkeleton />
      ) : (
        <ErrorBoundary level="section">
          <CatalogGridView
            entries={ctrl.visibleEntries}
            installedEntryIds={ctrl.installedEntryIds}
            onSelect={ctrl.handleSelect}
            onInstall={ctrl.handleInstall}
            emptyTitle={ctrl.emptyTitle}
            emptyDescription={ctrl.emptyDescription}
          />
        </ErrorBoundary>
      )}

      <CatalogDetailDrawer
        entry={ctrl.selectedEntry}
        installed={ctrl.selectedEntryInstalled}
        onClose={ctrl.closeSelected}
        onInstall={ctrl.handleSelectedInstall}
        onUninstall={ctrl.handleSelectedUninstall}
      />

      <McpInstallWizard onRequestCreateConnection={ctrl.setCreateConnectionType} />

      <ConnectionFormModal
        open={ctrl.createConnectionType !== null}
        mode="create"
        initialType={ctrl.createConnectionType ?? undefined}
        onClose={() => ctrl.setCreateConnectionType(null)}
      />
    </div>
  )
}
