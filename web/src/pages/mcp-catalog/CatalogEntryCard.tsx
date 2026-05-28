import type { McpCatalogEntry } from '@/api/types/integrations'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { CatalogEntryIcon } from './catalog-icons'

export interface CatalogEntryCardProps {
  entry: McpCatalogEntry
  installed: boolean
  onSelect: () => void
  onInstall: () => void
  className?: string
}

export function CatalogEntryCard({
  entry,
  installed,
  onSelect,
  onInstall,
  className,
}: CatalogEntryCardProps) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if ((e.key === 'Enter' || e.key === ' ') && e.target === e.currentTarget) {
          e.preventDefault()
          onSelect()
        }
      }}
      className={cn(
        'group flex h-full cursor-pointer flex-col gap-3 rounded-lg border border-border bg-card p-card text-left',
        'transition-all duration-200',
        'hover:bg-card-hover hover:-translate-y-px hover:shadow-[var(--so-shadow-card-hover)]',
        'focus:outline-none focus:ring-2 focus:ring-accent',
        className,
      )}
    >
      <CatalogEntryHeader entry={entry} installed={installed} />
      <p className="line-clamp-2 text-xs text-text-secondary">{entry.description}</p>
      <CatalogEntryTags tags={entry.tags} />
      <div className="mt-auto flex justify-end">
        <Button
          type="button"
          size="sm"
          variant={installed ? 'ghost' : 'default'}
          onClick={(event) => {
            event.stopPropagation()
            onInstall()
          }}
        >
          {installed ? 'Installed' : 'Install'}
        </Button>
      </div>
    </div>
  )
}

interface CatalogEntryHeaderProps {
  entry: McpCatalogEntry
  installed: boolean
}

function CatalogEntryHeader({ entry, installed }: CatalogEntryHeaderProps) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="flex items-center gap-3">
        <span
          className="flex size-10 shrink-0 items-center justify-center rounded-md bg-surface text-text-secondary"
          aria-hidden
        >
          <CatalogEntryIcon entryId={entry.id} className="size-5" />
        </span>
        <div className="flex flex-col">
          <span className="text-sm font-medium text-foreground">{entry.name}</span>
          {entry.required_connection_type !== null && (
            <span className="text-[11px] text-text-muted">
              Requires {entry.required_connection_type.replaceAll('_', ' ')}
            </span>
          )}
        </div>
      </div>
      {installed && (
        <span className="rounded-full bg-success/15 px-2 py-0.5 text-[11px] font-medium text-success">
          Installed
        </span>
      )}
    </div>
  )
}

interface CatalogEntryTagsProps {
  tags: readonly string[]
}

function CatalogEntryTags({ tags }: CatalogEntryTagsProps) {
  return (
    <div className="flex flex-wrap gap-1">
      {tags.slice(0, 4).map((tag) => (
        <span
          key={tag}
          className="rounded-full border border-border bg-surface px-2 py-0.5 text-[11px] text-text-muted"
        >
          {tag}
        </span>
      ))}
    </div>
  )
}
