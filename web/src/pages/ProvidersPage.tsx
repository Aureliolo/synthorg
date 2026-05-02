import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { probeLocal } from '@/api/endpoints/providers'
import { useProvidersData } from '@/hooks/useProvidersData'
import { useProvidersStore } from '@/stores/providers'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { PresetPickerSections } from '@/components/providers/PresetPickerSections'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import { ProviderGridView } from './providers/ProviderGridView'
import { ProviderFilters } from './providers/ProviderFilters'
import { ProvidersSkeleton } from './providers/ProvidersSkeleton'
import { ProviderFormModal } from './providers/ProviderFormModal'
import type { ProbePresetResponse, ProviderConfig } from '@/api/types/providers'

const log = createLogger('providers-page')

/**
 * Settings → Providers page.
 *
 * Top: configured providers list with filters.  Bottom: the same
 * three-section picker the wizard uses, so first-run and ongoing
 * management share UX.  The "Add Provider" verb is the picker itself
 * -- there is no separate dialog-launching button.
 */
export default function ProvidersPage() {
  const { filteredProviders, healthMap, loading, error, providers } = useProvidersData()
  const presets = useProvidersStore((s) => s.presets)
  const presetsLoading = useProvidersStore((s) => s.presetsLoading)
  const presetsError = useProvidersStore((s) => s.presetsError)
  const fetchPresets = useProvidersStore((s) => s.fetchPresets)
  const createFromPreset = useProvidersStore((s) => s.createFromPreset)
  const fetchProviders = useProvidersStore((s) => s.fetchProviders)

  const [modalOpen, setModalOpen] = useState(false)
  const [modalPreset, setModalPreset] = useState<string | null>(null)

  const [probeResults, setProbeResults] = useState<
    Readonly<Partial<Record<string, ProbePresetResponse>>>
  >({})
  const [probeErrors, setProbeErrors] = useState<
    Readonly<Partial<Record<string, string>>>
  >({})
  const [probing, setProbing] = useState(false)
  const [probeError, setProbeError] = useState<string | null>(null)

  // Cast the wire-shape providers list (full-detail config) into the
  // map of name → ProviderConfig that PresetPickerSections expects.
  // ``ProviderWithName`` extends ``ProviderConfig`` with a ``name`` so
  // this widening is safe.
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

  const runProbe = useCallback(async () => {
    setProbing(true)
    setProbeError(null)
    try {
      const response = await probeLocal()
      // Persist BOTH halves of the batch envelope: per-preset results
      // and per-preset errors are disjoint and both meaningful for the
      // detected-list UI.  Dropping ``response.errors`` would silently
      // hide unreachable local providers from the operator.
      const results = Object.fromEntries(
        Object.entries(response.results).filter(
          (entry): entry is [string, ProbePresetResponse] => entry[1] !== undefined,
        ),
      )
      const errors = Object.fromEntries(
        Object.entries(response.errors).filter(
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
    if (presets.length === 0) return
    probeStartedRef.current = true
    void runProbe()
  }, [presets.length, runProbe])

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

  const handleReprobe = useCallback(() => {
    void runProbe()
  }, [runProbe])

  const hasData = filteredProviders.length > 0 || providers.length > 0

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Providers"
        description="Configured LLM providers and presets your agents call."
        count={providers.length}
      />

      {error && (
        <ErrorBanner
          severity="error"
          title="Could not load providers"
          description={error}
        />
      )}

      <ProviderFilters />

      {loading && !hasData ? (
        <ProvidersSkeleton />
      ) : (
        <ErrorBoundary level="section">
          <ProviderGridView
            providers={filteredProviders}
            healthMap={healthMap}
            onAddProvider={handleConfigureManually}
          />
        </ErrorBoundary>
      )}

      <section
        aria-labelledby="add-provider-heading"
        className="space-y-section-gap border-t border-border pt-section-gap"
      >
        <h2 id="add-provider-heading" className="text-base font-semibold text-foreground">
          Add a provider
        </h2>

        {probeError && (
          <ErrorBanner
            severity="warning"
            title="Local provider probe did not complete"
            description={`${probeError} Re-scan to try again, or configure providers manually.`}
            onRetry={handleReprobe}
          />
        )}

        {presetsError && presets.length === 0 ? (
          <ErrorBanner
            title="Failed to load provider presets"
            description={presetsError}
            onRetry={() => void fetchPresets()}
          />
        ) : (
          <PresetPickerSections
            presets={presets}
            probeResults={probeResults}
            probeErrors={probeErrors}
            probing={probing || presetsLoading}
            providers={providersByName}
            onSelectCloud={handleSelectCloud}
            onAddLocal={handleAddLocal}
            onAddCloudCounterpart={handleAddCloudCounterpart}
            onReprobe={handleReprobe}
            onConfigureManually={handleConfigureManually}
          />
        )}
      </section>

      <ProviderFormModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        mode="create"
        initialPreset={modalPreset}
      />
    </div>
  )
}
