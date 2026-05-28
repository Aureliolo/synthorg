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
    <Drawer open onClose={onClose} title={entry.name} side="right">
      <div className="flex flex-col gap-4 p-card">
        <CatalogEntryHeader entry={entry} />
        <p className="text-sm text-text-secondary">{entry.description}</p>
        <DetailSection title="Required connection">
          <p className="text-sm text-foreground">
            {entry.required_connection_type
              ? entry.required_connection_type.replaceAll('_', ' ')
              : 'None (connectionless)'}
          </p>
        </DetailSection>
        <CapabilitiesSection capabilities={entry.capabilities} />
        <TagsSection tags={entry.tags} />
        {entry.npm_package && <InstallCommandSection npmPackage={entry.npm_package} />}
        <DrawerActions installed={installed} onInstall={onInstall} onUninstall={onUninstall} />
      </div>
    </Drawer>
  )
}

interface CatalogEntryHeaderProps {
  entry: McpCatalogEntry
}

function CatalogEntryHeader({ entry }: CatalogEntryHeaderProps) {
  return (
    <div className="flex items-start gap-3">
      <span
        className="flex size-12 shrink-0 items-center justify-center rounded-lg bg-surface text-text-secondary"
        aria-hidden
      >
        <CatalogEntryIcon entryId={entry.id} className="size-6" />
      </span>
      <div className="flex flex-col gap-1">
        <span className="text-base font-semibold text-foreground">{entry.name}</span>
        <code className="font-mono text-xs text-text-muted">{entry.id}</code>
      </div>
    </div>
  )
}

interface DetailSectionProps {
  title: string
  children: React.ReactNode
}

function DetailSection({ title, children }: DetailSectionProps) {
  return (
    <section className="flex flex-col gap-2">
      <h4 className="text-xs font-semibold uppercase text-text-muted">{title}</h4>
      {children}
    </section>
  )
}

interface CapabilitiesSectionProps {
  capabilities: readonly string[]
}

function CapabilitiesSection({ capabilities }: CapabilitiesSectionProps) {
  return (
    <DetailSection title="Capabilities">
      <ul className="flex flex-col gap-1">
        {capabilities.map((cap) => (
          <li
            key={cap}
            className="rounded-md bg-surface px-2 py-1 font-mono text-xs text-text-secondary"
          >
            {cap}
          </li>
        ))}
      </ul>
    </DetailSection>
  )
}

interface TagsSectionProps {
  tags: readonly string[]
}

function TagsSection({ tags }: TagsSectionProps) {
  return (
    <DetailSection title="Tags">
      <div className="flex flex-wrap gap-1">
        {tags.map((tag) => (
          <span
            key={tag}
            className="rounded-full border border-border bg-surface px-2 py-0.5 text-[11px] text-text-muted"
          >
            {tag}
          </span>
        ))}
      </div>
    </DetailSection>
  )
}

interface InstallCommandSectionProps {
  npmPackage: string
}

function InstallCommandSection({ npmPackage }: InstallCommandSectionProps) {
  return (
    <DetailSection title="Install command">
      <code className="rounded-md border border-border bg-surface px-2 py-2 font-mono text-xs text-text-secondary">
        npx -y {npmPackage}
      </code>
    </DetailSection>
  )
}

interface DrawerActionsProps {
  installed: boolean
  onInstall: () => void
  onUninstall: () => void
}

function DrawerActions({ installed, onInstall, onUninstall }: DrawerActionsProps) {
  return (
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
  )
}
