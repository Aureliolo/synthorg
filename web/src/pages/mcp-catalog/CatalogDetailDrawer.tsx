import type { McpCatalogEntry } from '@/api/types/integrations'
import { Button } from '@/components/ui/button'
import { Drawer } from '@/components/ui/drawer'
import { CatalogEntryIcon } from './catalog-icons'

export interface CatalogDetailDrawerProps {
  entry: McpCatalogEntry | null
  installed: boolean
  onClose: () => void
  onInstall: () => void
  onUninstall: () => void
}

export function CatalogDetailDrawer({
  entry,
  installed,
  onClose,
  onInstall,
  onUninstall,
}: CatalogDetailDrawerProps) {
  if (entry === null) {
    return (
      <Drawer open={false} onClose={onClose} ariaLabel="Catalog entry details">
        <div />
      </Drawer>
    )
  }

  return (
    <Drawer
      open={entry !== null}
      onClose={onClose}
      title={entry.name}
      side="right"
    >
      <div className="flex flex-col gap-4 p-card">
        <div className="flex items-start gap-3">
          <span
            className="flex size-12 shrink-0 items-center justify-center rounded-lg bg-surface text-text-secondary"
            aria-hidden
          >
            <CatalogEntryIcon entryId={entry.id} className="size-6" />
          </span>
          <div className="flex flex-col gap-1">
            <span className="text-base font-semibold text-foreground">
              {entry.name}
            </span>
            <code className="font-mono text-xs text-text-muted">
              {entry.id}
            </code>
          </div>
        </div>

        <p className="text-sm text-text-secondary">{entry.description}</p>

        <section className="flex flex-col gap-2">
          <h4 className="text-xs font-semibold uppercase text-text-muted">
            Required connection
          </h4>
          <p className="text-sm text-foreground">
            {entry.required_connection_type
              ? entry.required_connection_type.replaceAll('_', ' ')
              : 'None (connectionless)'}
          </p>
        </section>

        <section className="flex flex-col gap-2">
          <h4 className="text-xs font-semibold uppercase text-text-muted">
            Capabilities
          </h4>
          <ul className="flex flex-col gap-1">
            {entry.capabilities.map((cap) => (
              <li
                key={cap}
                className="rounded-md bg-surface px-2 py-1 font-mono text-xs text-text-secondary"
              >
                {cap}
              </li>
            ))}
          </ul>
        </section>

        <section className="flex flex-col gap-2">
          <h4 className="text-xs font-semibold uppercase text-text-muted">
            Tags
          </h4>
          <div className="flex flex-wrap gap-1">
            {entry.tags.map((tag) => (
              <span
                key={tag}
                className="rounded-full border border-border bg-surface px-2 py-0.5 text-[11px] text-text-muted"
              >
                {tag}
              </span>
            ))}
          </div>
        </section>

        {entry.npm_package && (
          <section className="flex flex-col gap-2">
            <h4 className="text-xs font-semibold uppercase text-text-muted">
              Install command
            </h4>
            <code className="rounded-md border border-border bg-surface px-2 py-2 font-mono text-xs text-text-secondary">
              npx -y {entry.npm_package}
            </code>
          </section>
        )}

        <div className="mt-2 flex flex-wrap justify-end gap-2">
          {installed ? (
            <Button
              type="button"
              variant="ghost"
              onClick={onUninstall}
              className="text-danger hover:text-danger"
            >
              Uninstall
            </Button>
          ) : (
            <Button type="button" onClick={onInstall}>
              Install
            </Button>
          )}
        </div>
      </div>
    </Drawer>
  )
}
