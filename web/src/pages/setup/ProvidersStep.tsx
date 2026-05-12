import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ErrorBanner } from '@/components/ui/error-banner'
import { Skeleton } from '@/components/ui/skeleton'
import { PresetPickerSections } from '@/components/providers/PresetPickerSections'
import { createLogger } from '@/lib/logger'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { useToastStore } from '@/stores/toast'
import { validateProvidersStep } from '@/utils/setup-validation'
import { ProviderFormModal, type ProviderFormOverrides } from '@/pages/providers/ProviderFormModal'

const log = createLogger('setup:providers-step')

/**
 * Wizard step: pick cloud presets, accept auto-detected local servers,
 * or configure custom endpoints.
 *
 * Layout follows the canonical three-section picker (Cloud / Detected
 * / Manual), reused on the Settings page so first-run and ongoing
 * management feel like one product.  All probe + create state lives in
 * ``useSetupWizardStore``; this component is a thin adapter.
 */
export function ProvidersStep() {
  const agents = useSetupWizardStore((s) => s.agents)
  const providers = useSetupWizardStore((s) => s.providers)
  const presets = useSetupWizardStore((s) => s.presets)
  const probeResults = useSetupWizardStore((s) => s.probeResults)
  const probeErrors = useSetupWizardStore((s) => s.probeErrors)
  const probeGlobalError = useSetupWizardStore((s) => s.probeGlobalError)
  const probing = useSetupWizardStore((s) => s.probing)
  const providersLoading = useSetupWizardStore((s) => s.providersLoading)
  const providersError = useSetupWizardStore((s) => s.providersError)
  const providersWarning = useSetupWizardStore((s) => s.providersWarning)
  const presetsLoading = useSetupWizardStore((s) => s.presetsLoading)
  const presetsError = useSetupWizardStore((s) => s.presetsError)

  const fetchProviders = useSetupWizardStore((s) => s.fetchProviders)
  const fetchPresets = useSetupWizardStore((s) => s.fetchPresets)
  const probeLocalProviders = useSetupWizardStore((s) => s.probeLocalProviders)
  const reprobeLocalProviders = useSetupWizardStore((s) => s.reprobeLocalProviders)
  const createProviderFromPreset = useSetupWizardStore((s) => s.createProviderFromPreset)
  const createProviderFromPresetFull = useSetupWizardStore((s) => s.createProviderFromPresetFull)
  const createProviderCustom = useSetupWizardStore((s) => s.createProviderCustom)
  const markStepComplete = useSetupWizardStore((s) => s.markStepComplete)
  const markStepIncomplete = useSetupWizardStore((s) => s.markStepIncomplete)

  // Modal state -- one modal, opened with a known preset (or null for
  // custom mode).  ``modalOpen`` toggles visibility; ``modalPreset``
  // captures which preset to pre-fill on open.
  const [modalOpen, setModalOpen] = useState(false)
  const [modalPreset, setModalPreset] = useState<string | null>(null)

  const fetchedRef = useRef(false)

  // Fetch providers and presets once on first mount (not on every re-render).
  // Clear any stale providersError that lingered from an earlier visit so the
  // step re-enters cleanly: the previous error came from a different attempt
  // and is misleading on a fresh remount, especially when the user navigated
  // back from a later wizard step.
  useEffect(() => {
    if (fetchedRef.current) return
    fetchedRef.current = true

    useSetupWizardStore.setState({ providersError: null })
    void fetchProviders()
    void fetchPresets()
  }, [fetchProviders, fetchPresets])

  // Auto-probe local presets once after presets are loaded
  const probeAttemptedRef = useRef(false)
  useEffect(() => {
    if (presets.length > 0 && !probing && !probeAttemptedRef.current) {
      probeAttemptedRef.current = true
      void probeLocalProviders()
    }
  }, [presets.length, probing, probeLocalProviders])

  // Track step completion
  const validation = useMemo(() => validateProvidersStep({ providers }), [providers])
  useEffect(() => {
    if (validation.valid) {
      markStepComplete('providers')
    } else {
      markStepIncomplete('providers')
    }
  }, [validation.valid, markStepComplete, markStepIncomplete])

  const handleSelectCloud = useCallback((presetName: string) => {
    setModalPreset(presetName)
    setModalOpen(true)
  }, [])

  const handleAddLocal = useCallback(
    async (presetName: string, detectedUrl: string) => {
      // Auto-add detected local providers using the detected URL.
      // Created provider keeps the preset's display_name as the
      // identifier so the configured-providers list matches what the
      // user just saw under "Detected on this machine".
      //
      // Branch on the result-object so a downstream fetchProviders
      // failure cannot retroactively make the create look like it
      // failed (the previous race read providersError after the
      // create returned, but providersError could be set BY the
      // refresh).
      const result = await createProviderFromPreset(
        presetName,
        presetName,
        undefined,
        detectedUrl,
      )
      if (result.ok) {
        // fetchProviders swallows its own errors and writes them to
        // providersError in the store, so a try/catch around it is
        // dead code; read the store after the call. If the refresh
        // failed, log and toast (so a dismissed toast still leaves
        // an observability trace) AND clear providersError so the
        // successfully-created provider is not surfaced as
        // "Failed to load providers" -- the create genuinely
        // succeeded; only the list refresh didn't.
        await fetchProviders()
        const fetchErrMsg = useSetupWizardStore.getState().providersError
        if (fetchErrMsg) {
          log.warn('fetch_providers_after_create_failed', {
            preset: presetName,
            error: fetchErrMsg,
          })
          useSetupWizardStore.setState({ providersError: null })
          useToastStore.getState().add({
            variant: 'warning',
            title: 'Provider added; could not refresh the list',
            description: fetchErrMsg,
          })
        }
      }
    },
    [createProviderFromPreset, fetchProviders],
  )

  const handleAddCloudCounterpart = useCallback((cloudPresetName: string) => {
    // Open the credential form pre-filled with the cloud counterpart
    // (e.g. ollama-cloud when "Add cloud" is clicked on the detected
    // local Ollama row).
    setModalPreset(cloudPresetName)
    setModalOpen(true)
  }, [])

  const handleConfigureManually = useCallback(() => {
    setModalPreset(null)
    setModalOpen(true)
  }, [])

  const handleReprobe = useCallback(async () => {
    probeAttemptedRef.current = true
    await reprobeLocalProviders()
  }, [reprobeLocalProviders])

  // Modal overrides so it talks to the wizard store, not the Settings store
  const modalOverrides: ProviderFormOverrides = useMemo(() => ({
    presets,
    presetsLoading,
    presetsError,
    onFetchPresets: fetchPresets,
    onCreateFromPreset: async (data) => {
      const result = await createProviderFromPresetFull(data)
      if (result && !useSetupWizardStore.getState().providersError) {
        await fetchProviders()
      }
      return result
    },
    onCreateProvider: async (data) => {
      const result = await createProviderCustom(data)
      if (result && !useSetupWizardStore.getState().providersError) {
        await fetchProviders()
      }
      return result
    },
  }), [presets, presetsLoading, presetsError, fetchPresets, createProviderFromPresetFull, createProviderCustom, fetchProviders])

  if (providersLoading && Object.keys(providers).length === 0) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 rounded-lg" />
        <Skeleton className="h-48 rounded-lg" />
      </div>
    )
  }

  // Which providers do agents need?
  const neededProviders = new Set(agents.map((a) => a.model_provider).filter((p): p is string => Boolean(p)))
  const missingProviders = [...neededProviders].filter((p) => !providers[p])

  const hasConfiguredProviders = Object.keys(providers).length > 0

  return (
    <div className="space-y-section-gap">
      <div className="space-y-2">
        <h2 className="text-lg font-semibold text-foreground">Set Up Providers</h2>
        <p className="text-sm text-muted-foreground">
          Connect your LLM providers so agents can work.
        </p>
      </div>

      {/* Guidance banner shown on first entry: explains the three options
          (cloud preset, detected local, manual) before the user picks
          one. Hidden once at least one provider is configured to avoid
          repeating itself on every revisit. */}
      {!hasConfiguredProviders && !presetsLoading && (
        <ErrorBanner
          variant="section"
          severity="info"
          title="Pick at least one provider to organise your agents"
          description={
            'Cloud presets sign you in with an API key. Detected local servers '
            + 'auto-fill from a probe of localhost. Configure manually for self-hosted '
            + 'endpoints. You can mix and match before continuing.'
          }
        />
      )}

      {providersError && (
        <ErrorBanner
          title="Failed to load providers"
          description={providersError}
          onRetry={() => void fetchProviders()}
        />
      )}

      {providersWarning && !providersError && (
        // Distinct from providersError: the provider was created OK,
        // only model discovery had an issue. Surfaced as a warning so
        // the user understands the create succeeded.
        <ErrorBanner
          severity="warning"
          title="Provider added with warnings"
          description={providersWarning}
          onRetry={() => void fetchProviders()}
        />
      )}

      {probeGlobalError && (
        <ErrorBanner
          title="Local provider probe did not complete"
          description={`${probeGlobalError} Re-scan to try again, or configure providers manually below.`}
          onRetry={handleReprobe}
        />
      )}

      {missingProviders.length > 0 && (
        <ErrorBanner
          severity="warning"
          title="Agents need providers that are not configured"
          description={`Missing: ${missingProviders.join(', ')}`}
        />
      )}

      {presetsLoading ? (
        <Skeleton className="h-32 rounded-lg" />
      ) : presetsError && presets.length === 0 ? (
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
          probing={probing}
          providers={providers}
          onSelectCloud={handleSelectCloud}
          onAddLocal={handleAddLocal}
          onAddCloudCounterpart={handleAddCloudCounterpart}
          onReprobe={handleReprobe}
          onConfigureManually={handleConfigureManually}
        />
      )}

      <ProviderFormModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        mode="create"
        initialPreset={modalPreset}
        overrides={modalOverrides}
      />

      {!validation.valid && validation.errors.length > 0 && (
        <ul className="space-y-1 text-xs text-muted-foreground">
          {validation.errors.map((err) => (
            <li key={err}>{err}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
