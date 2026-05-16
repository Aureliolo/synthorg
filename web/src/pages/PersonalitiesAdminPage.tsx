/**
 * Personalities admin page.
 *
 * Lists every personality preset available to setup-time and runtime
 * agent assignment, and exposes basic CRUD for custom (operator-
 * authored) presets. Built-in presets are read-only; the backend
 * surface (``/personalities/presets``) only accepts mutations for
 * the ``custom`` source so the runtime cannot drift from the shipped
 * defaults.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Loader2, Plus, Sparkles, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Drawer } from '@/components/ui/drawer'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { SectionCard } from '@/components/ui/section-card'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SearchInput } from '@/components/ui/search-input'
import { SelectField } from '@/components/ui/select-field'
import { TagInput } from '@/components/ui/tag-input'
import { useListPagination } from '@/hooks/use-list-pagination'
import { useToastStore } from '@/stores/toast'
import { paginateAll } from '@/api/client'
import {
  createAdminPreset,
  deleteAdminPreset,
  listAdminPresets,
} from '@/api/endpoints/personalities'
import type { PresetSummaryResponse } from '@/api/types/dtos.gen'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { getLocale } from '@/utils/locale'

const log = createLogger('PersonalitiesAdminPage')

// Hoisted out of render so ``navigator.language`` is read once at
// module init instead of inside ``useMemo`` -- ``@eslint-react/globals``
// flags in-render reads of ``navigator`` / ``window`` / ``document``.
const LOCALE = getLocale()

type PresetSortKey = 'name-asc' | 'name-desc'

const SORT_OPTIONS: ReadonlyArray<{ value: PresetSortKey; label: string }> = [
  { value: 'name-asc', label: 'Name (A-Z)' },
  { value: 'name-desc', label: 'Name (Z-A)' },
]

export default function PersonalitiesAdminPage() {
  const [presets, setPresets] = useState<readonly PresetSummaryResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [sortKey, setSortKey] = useState<PresetSortKey>('name-asc')

  // Create-drawer state.
  const [createOpen, setCreateOpen] = useState(false)
  const [createName, setCreateName] = useState('')
  const [createDescription, setCreateDescription] = useState('')
  const [createTraits, setCreateTraits] = useState<string[]>([])
  const [createSaving, setCreateSaving] = useState(false)

  // Delete-confirm state.
  const [deleteTarget, setDeleteTarget] = useState<PresetSummaryResponse | null>(null)
  const [deleting, setDeleting] = useState(false)

  const refresh = useCallback(async () => {
    setError(null)
    try {
      // ``listAdminPresets`` is single-page (cursor-aware) per the
      // MANDATORY pagination convention. This page does client-side
      // search/sort/filter, so it walks every page via ``paginateAll``
      // at the call site (capped internally to PAGINATE_ALL_MAX_PAGES)
      // to materialise the full set for the local index.
      const rows = await paginateAll<PresetSummaryResponse>((cursor) =>
        listAdminPresets({ cursor }),
      )
      setPresets(rows)
    } catch (err) {
      const message = getErrorMessage(err)
      log.error('listAdminPresets failed', { error: sanitizeForLog(message) })
      setError(message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => {
      if (cancelled) return
      setLoading(true)
      void refresh()
    })
    return () => { cancelled = true }
  }, [refresh])

  const visible = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    const matched = q
      ? presets.filter(
          (preset) =>
            preset.name.toLowerCase().includes(q)
            || preset.description.toLowerCase().includes(q),
        )
      : presets
    return [...matched].sort((a, b) => {
      const cmp = a.name.localeCompare(b.name, LOCALE)
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

  const handleCreateClose = useCallback(() => {
    setCreateOpen(false)
    setCreateName('')
    setCreateDescription('')
    setCreateTraits([])
  }, [])

  const handleCreateSubmit = useCallback(async () => {
    const trimmed = createName.trim()
    if (trimmed === '') return
    setCreateSaving(true)
    try {
      await createAdminPreset({
        name: trimmed,
        description: createDescription.trim(),
        traits: createTraits,
        // Numeric Big-Five axes default to 0.5 on the backend per
        // CreatePresetRequest defaults; sending them explicitly keeps
        // the wire shape predictable for tests that snapshot the body.
        agreeableness: 0.5,
        conscientiousness: 0.5,
        extraversion: 0.5,
        openness: 0.5,
        stress_response: 0.5,
        communication_style: 'neutral',
      })
      useToastStore.getState().add({
        variant: 'success',
        title: 'Personality preset created',
      })
      handleCreateClose()
      await refresh()
    } catch (err) {
      log.error('createAdminPreset failed', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to create personality preset'),
        description: getErrorMessage(err),
      })
    } finally {
      setCreateSaving(false)
    }
  }, [createName, createDescription, createTraits, refresh, handleCreateClose])

  const handleDeleteConfirm = useCallback(async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await deleteAdminPreset(deleteTarget.name)
      useToastStore.getState().add({
        variant: 'success',
        title: `Personality preset '${deleteTarget.name}' deleted`,
      })
      setDeleteTarget(null)
      await refresh()
    } catch (err) {
      log.error('deleteAdminPreset failed', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to delete personality preset'),
        description: getErrorMessage(err),
      })
    } finally {
      setDeleting(false)
    }
  }, [deleteTarget, refresh])

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Personality presets"
        count={totalItems}
        primaryAction={
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <Plus aria-hidden="true" />
            New custom preset
          </Button>
        }
      />

      {error && (
        <ErrorBanner severity="error" title="Could not load personality presets" description={error} />
      )}

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
          <SelectField
            label="Sort by"
            value={sortKey}
            onChange={(value) => setSortKey(value as PresetSortKey)}
            options={SORT_OPTIONS}
          />
        }
      />

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
              : 'Built-in presets ship with the runtime; add a custom preset with the button above.'
          }
        />
      ) : visible.length === 0 ? null : (
        <>
          <SectionCard title="Available presets" icon={Sparkles}>
            <ul className="grid grid-cols-1 gap-grid-gap md:grid-cols-2 lg:grid-cols-3">
              {pagedPresets.map((preset) => (
                <li key={preset.name}>
                  <article className="flex h-full flex-col rounded-lg border border-border bg-card p-card">
                    <div className="flex items-start justify-between gap-2">
                      <h3 className="text-sm font-semibold text-foreground">{preset.name}</h3>
                      <span className="rounded bg-surface-muted px-1.5 py-0.5 text-micro uppercase text-text-secondary">
                        {preset.source}
                      </span>
                    </div>
                    <p className="mt-2 flex-1 text-xs text-text-secondary">{preset.description}</p>
                    {preset.traits.length > 0 && (
                      <p className="mt-2 truncate text-micro text-text-muted" title={preset.traits.join(', ')}>
                        Traits: {preset.traits.join(', ')}
                      </p>
                    )}
                    {preset.source === 'custom' && (
                      <div className="mt-3 flex justify-end">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-danger hover:bg-danger/10"
                          onClick={() => setDeleteTarget(preset)}
                          aria-label={`Delete personality preset ${preset.name}`}
                        >
                          <Trash2 className="mr-1.5 size-3" aria-hidden="true" />
                          Delete
                        </Button>
                      </div>
                    )}
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

      <Drawer
        open={createOpen}
        onClose={handleCreateClose}
        title="New custom personality preset"
        width="narrow"
      >
        <div className="space-y-4">
          <p className="text-xs text-text-secondary">
            Big-Five axes default to 0.5 (neutral); refine via the API once the preset exists.
          </p>
          <InputField
            label="Name"
            value={createName}
            onValueChange={setCreateName}
            hint="Used as the preset identifier in agent configs."
          />
          <InputField
            label="Description"
            value={createDescription}
            onValueChange={setCreateDescription}
            hint="One-line summary shown in pickers."
          />
          <div>
            <span className="mb-1.5 block text-sm font-medium text-foreground">Traits</span>
            <TagInput
              value={createTraits}
              onChange={setCreateTraits}
              placeholder="Add a trait and press Enter"
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={handleCreateClose} disabled={createSaving}>
              Cancel
            </Button>
            <Button
              onClick={() => void handleCreateSubmit()}
              disabled={createSaving || createName.trim() === ''}
            >
              {createSaving ? 'Creating...' : 'Create preset'}
            </Button>
          </div>
        </div>
      </Drawer>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => { if (!open && !deleting) setDeleteTarget(null) }}
        title={`Delete personality preset '${deleteTarget?.name ?? ''}'?`}
        description="This permanently removes the custom preset. Agents currently assigned to it must be re-configured to use a different preset. This cannot be undone."
        confirmLabel="Delete"
        variant="destructive"
        loading={deleting}
        onConfirm={handleDeleteConfirm}
      />
    </div>
  )
}
