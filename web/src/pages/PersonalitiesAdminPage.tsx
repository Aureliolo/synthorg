/**
 * Personalities admin page.
 *
 * Lists every personality preset available to setup-time and
 * runtime agent assignment. Read-only today (the backend exposes
 * presets as a fixed list); the page is in place so the admin
 * surface can grow into a full CRUD view when bespoke presets ship.
 */
import { useEffect, useMemo, useState } from 'react'
import { Loader2, Sparkles } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { SectionCard } from '@/components/ui/section-card'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SearchInput } from '@/components/ui/search-input'
import { listPersonalityPresets } from '@/api/endpoints/setup'
import type { PersonalityPresetInfo } from '@/api/types/setup'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'

const log = createLogger('PersonalitiesAdminPage')

export default function PersonalitiesAdminPage() {
  const [presets, setPresets] = useState<readonly PersonalityPresetInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')

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
        log.error('listPersonalityPresets failed', { error: message })
        setError(message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })
    return () => { cancelled = true }
  }, [])

  const visible = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return presets
    return presets.filter(
      (preset) =>
        preset.name.toLowerCase().includes(q) ||
        preset.description.toLowerCase().includes(q),
    )
  }, [presets, searchQuery])

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Personality presets" count={visible.length} />

      <SearchFilterSort
        search={
          <SearchInput
            value={searchQuery}
            onChange={setSearchQuery}
            placeholder="Search personality presets"
            ariaLabel="Search personality presets"
          />
        }
      />

      {error && (
        <ErrorBanner severity="error" title="Could not load personality presets" description={error} />
      )}

      {loading && presets.length === 0 ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="size-6 animate-spin text-text-muted" />
        </div>
      ) : visible.length === 0 ? (
        <EmptyState
          icon={Sparkles}
          title={searchQuery ? 'No matching presets' : 'No personality presets configured'}
          description={
            searchQuery
              ? 'Try a different search term or clear the field above.'
              : 'Personality presets shipped with the runtime appear here.'
          }
        />
      ) : (
        <SectionCard title="Available presets" icon={Sparkles}>
          <ul className="grid grid-cols-1 gap-grid-gap md:grid-cols-2 lg:grid-cols-3">
            {visible.map((preset) => (
              <li key={preset.name}>
                <article className="rounded-lg border border-border bg-card p-card">
                  <h3 className="text-sm font-semibold text-foreground">{preset.name}</h3>
                  <p className="mt-2 text-xs text-text-secondary">{preset.description}</p>
                </article>
              </li>
            ))}
          </ul>
        </SectionCard>
      )}
    </div>
  )
}
