import { useMemo } from 'react'
import { DOC_TYPE_VALUES, type DocSummary, type DocType } from '@/api/types'

const DOC_TYPE_LABEL: Record<DocType, string> = {
  status_report: 'Status report',
  deliverable: 'Deliverable',
  knowledge_note: 'Note',
  codebase_analysis: 'Codebase analysis',
  run_narrative: 'Run narrative',
}

const DOC_TYPES = DOC_TYPE_VALUES

export interface DocListProps {
  docs: readonly DocSummary[]
  selectedSlug: string | null
  filter: DocType | null
  onSelect: (slug: string) => void
  onFilterChange: (filter: DocType | null) => void
}

export function DocList({
  docs,
  selectedSlug,
  filter,
  onSelect,
  onFilterChange,
}: DocListProps) {
  const filtered = useMemo(
    () =>
      filter === null ? docs : docs.filter((d) => d.doc_type === filter),
    [docs, filter],
  )

  return (
    <aside className="border-border flex flex-col gap-3 border-r pr-4">
      <div className="flex flex-wrap gap-2">
        <FilterChip
          label="All"
          active={filter === null}
          onClick={() => onFilterChange(null)}
        />
        {DOC_TYPES.map((kind) => (
          <FilterChip
            key={kind}
            label={DOC_TYPE_LABEL[kind]}
            active={filter === kind}
            onClick={() => onFilterChange(kind)}
          />
        ))}
      </div>
      {filtered.length === 0 ? (
        <p className="text-muted-foreground text-sm">No matching documents.</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {filtered.map((doc) => (
            <li key={doc.slug}>
              <button
                type="button"
                onClick={() => onSelect(doc.slug)}
                aria-pressed={selectedSlug === doc.slug}
                className={
                  selectedSlug === doc.slug
                    ? 'bg-muted text-foreground w-full rounded px-3 py-2 text-left'
                    : 'text-foreground/80 hover:bg-muted/50 w-full rounded px-3 py-2 text-left'
                }
              >
                <span className="block text-sm font-medium">{doc.title}</span>
                <span className="text-muted-foreground block text-xs">
                  {DOC_TYPE_LABEL[doc.doc_type]}
                  {doc.tags.length > 0
                    ? ` ${'·'} ${doc.tags.slice(0, 3).join(', ')}`
                    : ''}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </aside>
  )
}

export interface FilterChipProps {
  label: string
  active: boolean
  onClick: () => void
}

function FilterChip({ label, active, onClick }: FilterChipProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={
        active
          ? 'border-primary bg-primary/10 text-primary rounded-full border px-3 py-1 text-xs'
          : 'border-border text-foreground/80 hover:bg-muted/50 rounded-full border px-3 py-1 text-xs'
      }
    >
      {label}
    </button>
  )
}
