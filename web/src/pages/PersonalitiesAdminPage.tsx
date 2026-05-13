/**
 * Personalities admin page.
 *
 * Lists every personality preset available to setup-time and
 * runtime agent assignment. Read-only today (the backend exposes
 * presets as a fixed list); the page is in place so the admin
 * surface can grow into a full CRUD view when bespoke presets ship.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Loader2, Sparkles } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { SectionCard } from '@/components/ui/section-card'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SearchInput } from '@/components/ui/search-input'
import { useListPagination } from '@/hooks/use-list-pagination'
import { listPersonalityPresets } from '@/api/endpoints/setup'
import type { PersonalityPresetInfo } from '@/api/types/setup'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getErrorMessage } from '@/utils/errors'
import { getLocale } from '@/utils/locale'

const log = createLogger('PersonalitiesAdminPage')

type PresetSortKey = 'name-asc' | 'name-desc'

const SORT_OPTIONS: ReadonlyArray<{ value: PresetSortKey; label: string }> = [
  { value: 'name-asc', label: 'Name (A-Z)' },
  { value: 'name-desc', label: 'Name (Z-A)' },
]

export default function PersonalitiesAdminPage() {
  const [presets, setPresets] = useState<readonly PersonalityPresetInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [sortKey, setSortKey] = useState<PresetSortKey>('name-asc')

  useEffect(() => {
    let cancelled = false
    // Defer state writes to a microtask (per @eslint-react
    // set-state-in-effect) before issuing the fetch.
    void Promise.resolve().then(async () => {
      if (cancelled) return
      setLoading(true)
      setError(null)
      try {
        const rows = await listPersonalityPresets()
        if (!cancelled) setPresets(rows)
      } catch (err) {
        if (cancelled) return
        const message = getErrorMessage(err)
        // SEC-1: sanitize before structured logging.
        log.error('listPersonalityPresets failed', { error: sanitizeForLog(message) })
        setError(message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })
    return () => { cancelled = true }
  }, [])

  const visible = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    const matched = q
      ? presets.filter(
          (preset) =>
            preset.name.toLowerCase().includes(q) ||
            preset.description.toLowerCase().includes(q),
        )
      : presets
    const locale = getLocale()
    return [...matched].sort((a, b) => {
      const cmp = a.name.localeCompare(b.name, locale)
      return sortKey === 'name-asc' ? cmp : -cmp
    })
  }, [presets, searchQuery, sortKey])

  const {
    page,
    pageSize,
    totalItems,
    paginatedItems: pagedPresets,
    setPage,
    setPageSize,
    resetPage,
  } = useListPagination({
    items: visible,
    namespace: 'personalities',
    defaultPageSize: 24,
    pageSizeOptions: [12, 24, 48],
  })

  useEffect(() => {
    resetPage()
  }, [searchQuery, sortKey, resetPage])

  const handleSortChange = useCallback(
    (event: React.ChangeEvent<HTMLSelectElement>) => {
      setSortKey(event.target.value as PresetSortKey)
    },
    [],
  )

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Personality presets" count={totalItems} />

      <SearchFilterSort
        search={
          <SearchInput
            value={searchQuery}
            onChange={setSearchQuery}
            placeholder="Search personality presets"
            ariaLabel="Search personality presets"
          />
        }
        sort={
          <label className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">Sort by</span>
            <select
              value={sortKey}
              onChange={handleSortChange}
              aria-label="Sort personality presets"
              className="rounded-md border border-border bg-background px-2 py-1 text-sm"
            >
              {SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        }
      />

      {error && (
        <ErrorBanner severity="error" title="Could not load personality presets" description={error} />
      )}

      {loading && presets.length === 0 ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="size-6 animate-spin text-text-muted" />
        </div>
      ) : visible.length === 0 && error === null ? (
        <EmptyState
          icon={Sparkles}
          title={searchQuery ? 'No matching presets' : 'No personality presets configured'}
          description={
            searchQuery
              ? 'Try a different search term or clear the field above.'
              : 'Personality presets shipped with the runtime appear here.'
          }
        />
      ) : visible.length === 0 ? null : (
        <>
          <SectionCard title="Available presets" icon={Sparkles}>
            <ul className="grid grid-cols-1 gap-grid-gap md:grid-cols-2 lg:grid-cols-3">
              {pagedPresets.map((preset) => (
                <li key={preset.name}>
                  <article className="rounded-lg border border-border bg-card p-card">
                    <h3 className="text-sm font-semibold text-foreground">{preset.name}</h3>
                    <p className="mt-2 text-xs text-text-secondary">{preset.description}</p>
                  </article>
                </li>
              ))}
            </ul>
          </SectionCard>
          <Pagination
            page={page}
            pageSize={pageSize}
            total={totalItems}
            onPageChange={setPage}
            onPageSizeChange={setPageSize}
          />
        </>
      )}
    </div>
  )
}
