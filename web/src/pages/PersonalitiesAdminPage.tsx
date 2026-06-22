/**
 * Personalities admin page.
 *
 * Lists every personality preset available to setup-time and runtime agent
 * assignment, and exposes basic CRUD for custom (operator-authored) presets.
 * Built-in presets are read-only; the backend ``/personalities/presets``
 * surface only accepts mutations for the ``custom`` source so the runtime
 * cannot drift from the shipped defaults.
 */
import { Plus, Sparkles, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Drawer } from '@/components/ui/drawer'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { InputField } from '@/components/ui/input-field'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonCard } from '@/components/ui/skeleton'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SearchInput } from '@/components/ui/search-input'
import { SelectField } from '@/components/ui/select-field'
import { TagInput } from '@/components/ui/tag-input'
import type { PresetSummaryResponse } from '@/api/types'
import { makeEnumParser } from '@/utils/type-guards'

import {
  usePersonalitiesAdminController,
  type PersonalitiesAdminController,
  type PresetSortKey,
} from './personalities/usePersonalitiesAdminController'

const SORT_OPTIONS: ReadonlyArray<{ value: PresetSortKey; label: string }> = [
  { value: 'name-asc', label: 'Name (A-Z)' },
  { value: 'name-desc', label: 'Name (Z-A)' },
]

const parsePresetSortKey = makeEnumParser<PresetSortKey>(SORT_OPTIONS.map((o) => o.value))

export default function PersonalitiesAdminPage() {
  const ctrl = usePersonalitiesAdminController()
  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Personality presets"
        count={ctrl.pagination.totalItems}
        primaryAction={
          <Button size="sm" onClick={ctrl.openCreateDrawer}>
            <Plus aria-hidden="true" />
            New custom preset
          </Button>
        }
      />
      {ctrl.error && (
        <ErrorBanner
          severity="error"
          title="Could not load personality presets"
          description={ctrl.error}
        />
      )}
      <SearchFilterSort
        search={
          <SearchInput
            value={ctrl.searchQuery}
            onChange={ctrl.setSearchQuery}
            placeholder="Search personality presets"
            ariaLabel="Search personality presets"
          />
        }
        sort={
          <SelectField
            label="Sort by"
            value={ctrl.sortKey}
            onChange={(value) => {
              const key = parsePresetSortKey(value)
              if (key) ctrl.setSortKey(key)
            }}
            options={SORT_OPTIONS}
          />
        }
      />
      <PresetsBody ctrl={ctrl} />
      <CreatePresetDrawer ctrl={ctrl} />
      <ConfirmDialog
        open={ctrl.deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open && !ctrl.deleting) ctrl.setDeleteTarget(null)
        }}
        title={`Delete personality preset '${ctrl.deleteTarget?.name ?? ''}'?`}
        description="This permanently removes the custom preset. Agents currently assigned to it must be re-configured to use a different preset. This cannot be undone."
        confirmLabel="Delete"
        variant="destructive"
        loading={ctrl.deleting}
        onConfirm={ctrl.handleDeleteConfirm}
      />
    </div>
  )
}

interface CtrlProps {
  ctrl: PersonalitiesAdminController
}

function PresetsBody({ ctrl }: CtrlProps) {
  if (ctrl.loading && ctrl.presets.length === 0) {
    return (
      <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-2 lg:grid-cols-3">
        <SkeletonCard header lines={3} />
        <SkeletonCard header lines={3} />
        <SkeletonCard header lines={3} />
      </div>
    )
  }
  if (ctrl.visible.length === 0 && ctrl.error === null) {
    return (
      <EmptyState
        icon={Sparkles}
        title={ctrl.searchQuery ? 'No matching presets' : 'No personality presets configured'}
        description={
          ctrl.searchQuery
            ? 'Try a different search term or clear the field above.'
            : 'Built-in presets ship with the runtime; add a custom preset with the button above.'
        }
      />
    )
  }
  if (ctrl.visible.length === 0) return null
  return (
    <ErrorBoundary level="section">
      <SectionCard title="Available presets" icon={Sparkles}>
        <ul className="grid grid-cols-1 gap-grid-gap md:grid-cols-2 lg:grid-cols-3">
          {ctrl.pagination.paginatedItems.map((preset) => (
            <PresetCardListItem
              key={preset.name}
              preset={preset}
              onRequestDelete={ctrl.setDeleteTarget}
            />
          ))}
        </ul>
      </SectionCard>
      <Pagination
        page={ctrl.pagination.page}
        pageSize={ctrl.pagination.pageSize}
        total={ctrl.pagination.totalItems}
        onPageChange={ctrl.pagination.setPage}
        onPageSizeChange={ctrl.pagination.setPageSize}
      />
    </ErrorBoundary>
  )
}

interface PresetCardListItemProps {
  preset: PresetSummaryResponse
  onRequestDelete: (preset: PresetSummaryResponse) => void
}

function PresetCardListItem({ preset, onRequestDelete }: PresetCardListItemProps) {
  return (
    <li>
      <article className="flex h-full flex-col rounded-lg border border-border bg-card p-card">
        <div className="flex items-start justify-between gap-2">
          <h3 className="text-sm font-semibold text-foreground">{preset.name}</h3>
          <span className="rounded bg-surface-muted px-1.5 py-0.5 text-micro uppercase text-text-secondary">
            {preset.source}
          </span>
        </div>
        <p className="mt-2 flex-1 text-xs text-text-secondary">{preset.description}</p>
        {preset.traits.length > 0 && (
          <p
            className="mt-2 truncate text-micro text-text-muted"
            title={preset.traits.join(', ')}
          >
            Traits: {preset.traits.join(', ')}
          </p>
        )}
        {preset.source === 'custom' && (
          <div className="mt-3 flex justify-end">
            <Button
              variant="ghost"
              size="sm"
              className="text-danger hover:bg-danger/10"
              onClick={() => onRequestDelete(preset)}
              aria-label={`Delete personality preset ${preset.name}`}
            >
              <Trash2 className="mr-1.5 size-3" aria-hidden="true" />
              Delete
            </Button>
          </div>
        )}
      </article>
    </li>
  )
}

function CreatePresetDrawer({ ctrl }: CtrlProps) {
  return (
    <Drawer
      open={ctrl.createOpen}
      onClose={ctrl.handleCreateClose}
      title="New custom personality preset"
      width="narrow"
    >
      <div className="space-y-4">
        <p className="text-xs text-text-secondary">
          Big-Five axes default to 0.5 (neutral); refine via the API once the preset
          exists.
        </p>
        <InputField
          label="Name"
          value={ctrl.createName}
          onValueChange={ctrl.setCreateName}
          hint="Used as the preset identifier in agent configs."
        />
        <InputField
          label="Description"
          value={ctrl.createDescription}
          onValueChange={ctrl.setCreateDescription}
          hint="One-line summary shown in pickers."
        />
        <div>
          <span className="mb-1.5 block text-sm font-medium text-foreground">Traits</span>
          <TagInput
            value={ctrl.createTraits}
            onChange={ctrl.setCreateTraits}
            placeholder="Add a trait and press Enter"
          />
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <Button
            variant="outline"
            onClick={ctrl.handleCreateClose}
            disabled={ctrl.createSaving}
          >
            Cancel
          </Button>
          <Button
            onClick={() => void ctrl.handleCreateSubmit()}
            disabled={ctrl.createSaving || ctrl.createName.trim() === ''}
          >
            {ctrl.createSaving ? 'Creating...' : 'Create preset'}
          </Button>
        </div>
      </div>
    </Drawer>
  )
}
