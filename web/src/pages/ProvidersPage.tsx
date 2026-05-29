import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { probeLocal } from '@/api/endpoints/providers'
import { useProvidersData } from '@/hooks/useProvidersData'
import { useProvidersStore } from '@/stores/providers'
import { AnimatePresence } from 'motion/react'
import { Trash2 } from 'lucide-react'
import { BulkActionBar } from '@/components/ui/bulk-action-bar'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { useListPagination } from '@/hooks/use-list-pagination'
import { PresetPickerSections } from '@/components/providers/PresetPickerSections'
import { formatNumber } from '@/utils/format'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import { ProviderGridView } from './providers/ProviderGridView'
import { ProviderFilters } from './providers/ProviderFilters'
import { ProvidersSkeleton } from './providers/ProvidersSkeleton'
import { ProviderFormModal } from './providers/ProviderFormModal'
import type { ProbePresetResponse, ProviderConfig } from '@/api/types/providers'

const log = createLogger('providers-page')

type ProviderList = ReturnType<typeof useProvidersData>['filteredProviders']

interface ProviderProbe {
  probeResults: Readonly<Partial<Record<string, ProbePresetResponse>>>
  probeErrors: Readonly<Partial<Record<string, string>>>
  probing: boolean
  probeError: string | null
  handleReprobe: () => void
}

function useProviderProbe(presetsLength: number): ProviderProbe {
  const [probeResults, setProbeResults] = useState<
    Readonly<Partial<Record<string, ProbePresetResponse>>>
  >({})
  const [probeErrors, setProbeErrors] = useState<Readonly<Partial<Record<string, string>>>>({})
  const [probing, setProbing] = useState(false)
  const [probeError, setProbeError] = useState<string | null>(null)

  const runProbe = useCallback(async () => {
    setProbing(true)
    setProbeError(null)
    try {
      const response = await probeLocal()
      // Persist BOTH halves of the batch envelope: per-preset results
      // and per-preset errors are disjoint and both meaningful for the
      // detected-list UI. Dropping ``response.errors`` would silently
      // hide unreachable local providers from the operator.
      const results = Object.fromEntries(
        Object.entries(response.results ?? {}).filter(
          (entry): entry is [string, ProbePresetResponse] => entry[1] !== undefined,
        ),
      )
      const errors = Object.fromEntries(
        Object.entries(response.errors ?? {}).filter(
          (entry): entry is [string, string] => entry[1] !== undefined,
        ),
      )
      setProbeResults(results)
      setProbeErrors(errors)
    } catch (err) {
      const msg = getErrorMessage(err)
      log.error('probe-local failed', msg)
      setProbeError(msg)
    } finally {
      setProbing(false)
    }
  }, [])

  const probeStartedRef = useRef(false)
  useEffect(() => {
    if (probeStartedRef.current) return
    if (presetsLength === 0) return
    probeStartedRef.current = true
    void runProbe()
  }, [presetsLength, runProbe])

  const handleReprobe = useCallback(() => {
    void runProbe()
  }, [runProbe])

  return { probeResults, probeErrors, probing, probeError, handleReprobe }
}

interface ProviderPickerModal {
  modalOpen: boolean
  setModalOpen: (open: boolean) => void
  modalPreset: string | null
  handleSelectCloud: (presetName: string) => void
  handleAddLocal: (presetName: string, detectedUrl: string) => Promise<void>
  handleAddCloudCounterpart: (cloudPresetName: string) => void
  handleConfigureManually: () => void
}

function useProviderPickerModal(): ProviderPickerModal {
  const createFromPreset = useProvidersStore((s) => s.createFromPreset)
  const fetchProviders = useProvidersStore((s) => s.fetchProviders)
  const [modalOpen, setModalOpen] = useState(false)
  const [modalPreset, setModalPreset] = useState<string | null>(null)

  const handleSelectCloud = useCallback((presetName: string) => {
    setModalPreset(presetName)
    setModalOpen(true)
  }, [])

  const handleAddLocal = useCallback(
    async (presetName: string, detectedUrl: string) => {
      const created = await createFromPreset({
        preset_name: presetName,
        name: presetName,
        base_url: detectedUrl,
        auth_type: 'none',
        tos_accepted: false,
      })
      if (created) {
        await fetchProviders()
      }
    },
    [createFromPreset, fetchProviders],
  )

  const handleAddCloudCounterpart = useCallback((cloudPresetName: string) => {
    setModalPreset(cloudPresetName)
    setModalOpen(true)
  }, [])

  const handleConfigureManually = useCallback(() => {
    setModalPreset(null)
    setModalOpen(true)
  }, [])

  return {
    modalOpen, setModalOpen, modalPreset, handleSelectCloud, handleAddLocal,
    handleAddCloudCounterpart, handleConfigureManually,
  }
}

interface ProviderSelection {
  visibleSelected: ReadonlySet<string>
  selectedCount: number
  handleToggleSelect: (name: string) => void
  clearSelection: () => void
  bulkDeleteOpen: boolean
  setBulkDeleteOpen: (open: boolean) => void
  bulkDeleting: boolean
  handleBulkDelete: () => Promise<void>
}

function useProviderSelection(filteredProviders: ProviderList): ProviderSelection {
  const bulkDeleteProviders = useProvidersStore((s) => s.bulkDeleteProviders)
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(() => new Set())
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false)
  const [bulkDeleting, setBulkDeleting] = useState(false)

  const handleToggleSelect = useCallback((name: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }, [])
  const clearSelection = useCallback(() => setSelectedIds(new Set()), [])

  const visibleSelected = useMemo(() => {
    const visible = new Set(filteredProviders.map((p) => p.name))
    const next = new Set<string>()
    for (const name of selectedIds) {
      if (visible.has(name)) next.add(name)
    }
    return next
  }, [selectedIds, filteredProviders])

  const handleBulkDelete = useCallback(async () => {
    // Page owns only UI state; the store action owns the API loop +
    // aggregate toast (callers must not wrap store mutations in
    // try/catch or duplicate the toast UX).
    setBulkDeleting(true)
    try {
      await bulkDeleteProviders([...visibleSelected])
    } finally {
      setBulkDeleting(false)
      setBulkDeleteOpen(false)
      clearSelection()
    }
  }, [visibleSelected, bulkDeleteProviders, clearSelection])

  return {
    visibleSelected,
    selectedCount: visibleSelected.size,
    handleToggleSelect,
    clearSelection,
    bulkDeleteOpen,
    setBulkDeleteOpen,
    bulkDeleting,
    handleBulkDelete,
  }
}

function ProvidersBulkActions({ sel }: { sel: ProviderSelection }) {
  return (
    <>
      <AnimatePresence>
        {sel.selectedCount > 0 && (
          <BulkActionBar
            selectedCount={sel.selectedCount}
            onClear={sel.clearSelection}
            loading={sel.bulkDeleting}
            ariaLabel="Provider bulk actions"
          >
            <Button
              size="sm"
              variant="outline"
              className="gap-1 border-danger/30 text-danger hover:bg-danger/10"
              onClick={() => sel.setBulkDeleteOpen(true)}
              disabled={sel.bulkDeleting}
            >
              <Trash2 className="size-3.5" />
              Delete {formatNumber(sel.selectedCount)}
            </Button>
          </BulkActionBar>
        )}
      </AnimatePresence>

      <ConfirmDialog
        open={sel.bulkDeleteOpen}
        onOpenChange={(open) => { if (!open && !sel.bulkDeleting) sel.setBulkDeleteOpen(false) }}
        title={`Delete ${formatNumber(sel.selectedCount)} provider${sel.selectedCount === 1 ? '' : 's'}?`}
        description="Each provider is removed via its individual delete endpoint. Agents bound to these providers will fail until reassigned. This cannot be undone."
        confirmLabel={`Delete ${formatNumber(sel.selectedCount)}`}
        variant="destructive"
        loading={sel.bulkDeleting}
        onConfirm={sel.handleBulkDelete}
      />
    </>
  )
}

interface AddProviderSectionProps {
  probe: ProviderProbe
  presets: ReturnType<typeof useProvidersStore.getState>['presets']
  presetsLoading: boolean
  presetsError: string | null
  fetchPresets: () => void
  providersByName: Readonly<Record<string, ProviderConfig>>
  modal: ProviderPickerModal
}

function AddProviderSection({
  probe,
  presets,
  presetsLoading,
  presetsError,
  fetchPresets,
  providersByName,
  modal,
}: AddProviderSectionProps) {
  return (
    <section
      aria-labelledby="add-provider-heading"
      className="space-y-section-gap border-t border-border pt-section-gap"
    >
      <h2 id="add-provider-heading" className="text-base font-semibold text-foreground">
        Add a provider
      </h2>

      {probe.probeError && (
        <ErrorBanner
          severity="warning"
          title="Local provider probe did not complete"
          description={`${probe.probeError} Re-scan to try again, or configure providers manually.`}
          onRetry={probe.handleReprobe}
        />
      )}

      {presetsError && presets.length === 0 ? (
        <ErrorBanner
          title="Failed to load provider presets"
          description={presetsError}
          onRetry={fetchPresets}
        />
      ) : (
        <PresetPickerSections
          presets={presets}
          probeResults={probe.probeResults}
          probeErrors={probe.probeErrors}
          probing={probe.probing || presetsLoading}
          providers={providersByName}
          onSelectCloud={modal.handleSelectCloud}
          onAddLocal={modal.handleAddLocal}
          onAddCloudCounterpart={modal.handleAddCloudCounterpart}
          onReprobe={probe.handleReprobe}
          onConfigureManually={modal.handleConfigureManually}
        />
      )}
    </section>
  )
}

/**
 * Settings → Providers page.
 *
 * Top: configured providers list with filters.  Bottom: the same
 * three-section picker the wizard uses, so first-run and ongoing
 * management share UX.  The "Add Provider" verb is the picker itself
 * -- there is no separate dialog-launching button.
 */
export default function ProvidersPage() {
  const { filteredProviders, healthMap, loading, error, providers, isRefetching } = useProvidersData()
  const presets = useProvidersStore((s) => s.presets)
  const presetsLoading = useProvidersStore((s) => s.presetsLoading)
  const presetsError = useProvidersStore((s) => s.presetsError)
  const fetchPresets = useProvidersStore((s) => s.fetchPresets)

  // Cast the wire-shape providers list (full-detail config) into the
  // map of name → ProviderConfig that PresetPickerSections expects.
  const providersByName = useMemo<Readonly<Record<string, ProviderConfig>>>(() => {
    const out: Record<string, ProviderConfig> = {}
    for (const p of providers) {
      out[p.name] = p
    }
    return out
  }, [providers])

  const presetsFetchedRef = useRef(false)
  useEffect(() => {
    if (presetsFetchedRef.current) return
    presetsFetchedRef.current = true
    void fetchPresets()
  }, [fetchPresets])

  const probe = useProviderProbe(presets.length)
  const modal = useProviderPickerModal()
  const pagination = useListPagination({ items: filteredProviders, namespace: 'providers' })
  const sel = useProviderSelection(filteredProviders)

  const hasData = filteredProviders.length > 0 || providers.length > 0

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Providers"
        description="Configured LLM providers and presets your agents call."
        count={providers.length}
        refreshing={isRefetching}
      />

      {error && (
        <ErrorBanner severity="error" title="Could not load providers" description={error} />
      )}

      <ProviderFilters />

      {loading && !hasData ? (
        <ProvidersSkeleton />
      ) : (
        <ErrorBoundary level="section">
          <ProviderGridView
            providers={pagination.paginatedItems}
            healthMap={healthMap}
            onAddProvider={modal.handleConfigureManually}
            selectedIds={sel.visibleSelected}
            onToggleSelect={sel.handleToggleSelect}
          />
          <Pagination
            page={pagination.page}
            pageSize={pagination.pageSize}
            total={pagination.totalItems}
            onPageChange={pagination.setPage}
            onPageSizeChange={pagination.setPageSize}
          />
        </ErrorBoundary>
      )}

      <ProvidersBulkActions sel={sel} />

      <AddProviderSection
        probe={probe}
        presets={presets}
        presetsLoading={presetsLoading}
        presetsError={presetsError}
        fetchPresets={() => void fetchPresets()}
        providersByName={providersByName}
        modal={modal}
      />

      <ProviderFormModal
        open={modal.modalOpen}
        onClose={() => modal.setModalOpen(false)}
        mode="create"
        initialPreset={modal.modalPreset}
      />
    </div>
  )
}
