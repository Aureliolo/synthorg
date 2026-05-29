import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ErrorBanner } from '@/components/ui/error-banner'
import { Skeleton } from '@/components/ui/skeleton'
import { PresetPickerSections } from '@/components/providers/PresetPickerSections'
import { createLogger } from '@/lib/logger'
import { useSetupWizardStore } from '@/stores/setup-wizard'
import { useToastStore } from '@/stores/toast'
import { validateProvidersStep } from '@/utils/setup-validation'
import { useStepCompletionSync } from './_hooks'
import { ProviderFormModal } from '@/pages/providers/ProviderFormModal'
import type { ProviderFormOverrides } from '@/pages/providers/provider-form-helpers'

const log = createLogger('setup:providers-step')

type ProvidersValidation = ReturnType<typeof validateProvidersStep>

/**
 * Auto-add a detected local provider using its probed URL. The created
 * provider keeps the preset's display_name as the identifier so the
 * configured-providers list matches what the user saw under "Detected on
 * this machine". Branches on the create result-object so a downstream
 * fetchProviders failure cannot retroactively make the create look like
 * it failed (fetchProviders swallows its own errors into providersError).
 */
async function addDetectedLocalProvider(presetName: string, detectedUrl: string): Promise<void> {
  const result = await useSetupWizardStore
    .getState()
    .createProviderFromPreset(presetName, presetName, undefined, detectedUrl)
  if (!result.ok) return
  await useSetupWizardStore.getState().fetchProviders()
  const fetchErrMsg = useSetupWizardStore.getState().providersError
  if (!fetchErrMsg) return
  // The create genuinely succeeded; only the list refresh failed. Log +
  // toast (so a dismissed toast still leaves an observability trace) AND
  // clear providersError so the new provider is not surfaced as
  // "Failed to load providers".
  log.warn('fetch_providers_after_create_failed', { preset: presetName, error: fetchErrMsg })
  useSetupWizardStore.setState({ providersError: null })
  useToastStore.getState().add({
    variant: 'warning',
    title: 'Provider added; could not refresh the list',
    description: fetchErrMsg,
  })
}

/** Run a create mutation, then refresh the list unless the create poisoned it. */
async function createProviderThenRefresh<T>(create: () => Promise<T>): Promise<T> {
  const result = await create()
  if (result && !useSetupWizardStore.getState().providersError) {
    await useSetupWizardStore.getState().fetchProviders()
  }
  return result
}

/** Modal overrides so the form talks to the wizard store, not Settings. */
function buildProvidersModalOverrides(presetState: {
  presets: ProviderFormOverrides['presets']
  presetsLoading: boolean
  presetsError: string | null
}): ProviderFormOverrides {
  return {
    ...presetState,
    onFetchPresets: () => {
      void useSetupWizardStore.getState().fetchPresets()
    },
    onCreateFromPreset: (data) =>
      createProviderThenRefresh(() => useSetupWizardStore.getState().createProviderFromPresetFull(data)),
    onCreateProvider: (data) =>
      createProviderThenRefresh(() => useSetupWizardStore.getState().createProviderCustom(data)),
  }
}

interface ProvidersStepController {
  agents: ReturnType<typeof useSetupWizardStore.getState>['agents']
  providers: ReturnType<typeof useSetupWizardStore.getState>['providers']
  presets: ReturnType<typeof useSetupWizardStore.getState>['presets']
  probeResults: ReturnType<typeof useSetupWizardStore.getState>['probeResults']
  probeErrors: ReturnType<typeof useSetupWizardStore.getState>['probeErrors']
  probeGlobalError: string | null
  probing: boolean
  providersLoading: boolean
  providersError: string | null
  providersWarning: string | null
  presetsLoading: boolean
  presetsError: string | null
  validation: ProvidersValidation
  missingProviders: string[]
  hasConfiguredProviders: boolean
  modalOpen: boolean
  modalPreset: string | null
  setModalOpen: (open: boolean) => void
  modalOverrides: ProviderFormOverrides
  handleSelectCloud: (presetName: string) => void
  handleAddLocal: (presetName: string, detectedUrl: string) => Promise<void>
  handleAddCloudCounterpart: (cloudPresetName: string) => void
  handleConfigureManually: () => void
  handleReprobe: () => Promise<void>
  onRetryProviders: () => void
  onRetryPresets: () => void
}

function useProvidersStepController(): ProvidersStepController {
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

  const [modalOpen, setModalOpen] = useState(false)
  const [modalPreset, setModalPreset] = useState<string | null>(null)
  const fetchedRef = useRef(false)
  const probeAttemptedRef = useRef(false)

  // Fetch providers and presets once on first mount. Clear any stale
  // providersError from an earlier visit so the step re-enters cleanly.
  useEffect(() => {
    if (fetchedRef.current) return
    fetchedRef.current = true
    useSetupWizardStore.setState({ providersError: null })
    void useSetupWizardStore.getState().fetchProviders()
    void useSetupWizardStore.getState().fetchPresets()
  }, [])

  // Auto-probe local presets once after presets are loaded.
  useEffect(() => {
    if (presets.length > 0 && !probing && !probeAttemptedRef.current) {
      probeAttemptedRef.current = true
      void useSetupWizardStore.getState().probeLocalProviders()
    }
  }, [presets.length, probing])

  const validation = useMemo(() => validateProvidersStep({ providers }), [providers])
  useStepCompletionSync('providers', validation.valid)

  const handleSelectCloud = useCallback((presetName: string) => {
    setModalPreset(presetName)
    setModalOpen(true)
  }, [])

  const handleAddLocal = useCallback(
    (presetName: string, detectedUrl: string) => addDetectedLocalProvider(presetName, detectedUrl),
    [],
  )

  const handleAddCloudCounterpart = useCallback((cloudPresetName: string) => {
    setModalPreset(cloudPresetName)
    setModalOpen(true)
  }, [])

  const handleConfigureManually = useCallback(() => {
    setModalPreset(null)
    setModalOpen(true)
  }, [])

  const handleReprobe = useCallback(async () => {
    probeAttemptedRef.current = true
    await useSetupWizardStore.getState().reprobeLocalProviders()
  }, [])

  const modalOverrides = useMemo(
    () => buildProvidersModalOverrides({ presets, presetsLoading, presetsError }),
    [presets, presetsLoading, presetsError],
  )

  const neededProviders = new Set(
    agents.map((a) => a.model_provider).filter((p): p is string => Boolean(p)),
  )
  const missingProviders = [...neededProviders].filter((p) => !providers[p])
  const hasConfiguredProviders = Object.keys(providers).length > 0

  return {
    agents, providers, presets, probeResults, probeErrors, probeGlobalError, probing,
    providersLoading, providersError, providersWarning, presetsLoading, presetsError,
    validation, missingProviders, hasConfiguredProviders,
    modalOpen, modalPreset, setModalOpen, modalOverrides,
    handleSelectCloud, handleAddLocal, handleAddCloudCounterpart, handleConfigureManually, handleReprobe,
    onRetryProviders: () => void useSetupWizardStore.getState().fetchProviders(),
    onRetryPresets: () => void useSetupWizardStore.getState().fetchPresets(),
  }
}

/**
 * First-entry guidance: explains the three options (cloud preset,
 * detected local, manual) before the user picks one. Hidden once at
 * least one provider is configured to avoid repeating itself on revisit.
 */
function ProvidersGuidanceBanner({
  hasConfiguredProviders,
  presetsLoading,
}: {
  hasConfiguredProviders: boolean
  presetsLoading: boolean
}) {
  if (hasConfiguredProviders || presetsLoading) return null
  return (
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
  )
}

interface ProvidersStepBannersProps {
  providersError: string | null
  providersWarning: string | null
  probeGlobalError: string | null
  missingProviders: readonly string[]
  onRetryProviders: () => void
  onReprobe: () => void
}

function ProvidersStepBanners({
  providersError,
  providersWarning,
  probeGlobalError,
  missingProviders,
  onRetryProviders,
  onReprobe,
}: ProvidersStepBannersProps) {
  return (
    <>
      {providersError && (
        <ErrorBanner
          title="Failed to load providers"
          description={providersError}
          onRetry={onRetryProviders}
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
          onRetry={onRetryProviders}
        />
      )}

      {probeGlobalError && (
        <ErrorBanner
          title="Local provider probe did not complete"
          description={`${probeGlobalError} Re-scan to try again, or configure providers manually below.`}
          onRetry={onReprobe}
        />
      )}

      {missingProviders.length > 0 && (
        <ErrorBanner
          severity="warning"
          title="Agents need providers that are not configured"
          description={`Missing: ${missingProviders.join(', ')}`}
        />
      )}
    </>
  )
}

interface ProvidersPresetSectionProps {
  presetsLoading: boolean
  presetsError: string | null
  presets: ProvidersStepController['presets']
  probeResults: ProvidersStepController['probeResults']
  probeErrors: ProvidersStepController['probeErrors']
  probing: boolean
  providers: ProvidersStepController['providers']
  onSelectCloud: (presetName: string) => void
  onAddLocal: (presetName: string, detectedUrl: string) => Promise<void>
  onAddCloudCounterpart: (cloudPresetName: string) => void
  onReprobe: () => Promise<void>
  onConfigureManually: () => void
  onRetryPresets: () => void
}

function ProvidersPresetSection({
  presetsLoading,
  presetsError,
  presets,
  probeResults,
  probeErrors,
  probing,
  providers,
  onSelectCloud,
  onAddLocal,
  onAddCloudCounterpart,
  onReprobe,
  onConfigureManually,
  onRetryPresets,
}: ProvidersPresetSectionProps) {
  if (presetsLoading) {
    return <Skeleton className="h-32 rounded-lg" />
  }
  if (presetsError && presets.length === 0) {
    return (
      <ErrorBanner
        title="Failed to load provider presets"
        description={presetsError}
        onRetry={onRetryPresets}
      />
    )
  }
  return (
    <PresetPickerSections
      presets={presets}
      probeResults={probeResults}
      probeErrors={probeErrors}
      probing={probing}
      providers={providers}
      onSelectCloud={onSelectCloud}
      onAddLocal={onAddLocal}
      onAddCloudCounterpart={onAddCloudCounterpart}
      onReprobe={onReprobe}
      onConfigureManually={onConfigureManually}
    />
  )
}

function ProvidersValidationErrors({ validation }: { validation: ProvidersValidation }) {
  if (validation.valid || validation.errors.length === 0) return null
  return (
    <ul className="space-y-1 text-xs text-muted-foreground">
      {validation.errors.map((err) => (
        <li key={err}>{err}</li>
      ))}
    </ul>
  )
}

/**
 * Wizard step: pick cloud presets, accept auto-detected local servers,
 * or configure custom endpoints.
 *
 * Layout follows the canonical three-section picker (Cloud / Detected
 * / Manual), reused on the Settings page so first-run and ongoing
 * management feel like one product. All probe + create state lives in
 * ``useSetupWizardStore``; this component is a thin adapter.
 */
export function ProvidersStep() {
  const c = useProvidersStepController()

  if (c.providersLoading && Object.keys(c.providers).length === 0) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 rounded-lg" />
        <Skeleton className="h-48 rounded-lg" />
      </div>
    )
  }

  return (
    <div className="space-y-section-gap">
      <div className="space-y-2">
        <h2 className="text-lg font-semibold text-foreground">Set Up Providers</h2>
        <p className="text-sm text-muted-foreground">
          Connect your LLM providers so agents can work.
        </p>
      </div>

      <ProvidersGuidanceBanner
        hasConfiguredProviders={c.hasConfiguredProviders}
        presetsLoading={c.presetsLoading}
      />

      <ProvidersStepBanners
        providersError={c.providersError}
        providersWarning={c.providersWarning}
        probeGlobalError={c.probeGlobalError}
        missingProviders={c.missingProviders}
        onRetryProviders={c.onRetryProviders}
        onReprobe={c.handleReprobe}
      />

      <ProvidersPresetSection
        presetsLoading={c.presetsLoading}
        presetsError={c.presetsError}
        presets={c.presets}
        probeResults={c.probeResults}
        probeErrors={c.probeErrors}
        probing={c.probing}
        providers={c.providers}
        onSelectCloud={c.handleSelectCloud}
        onAddLocal={c.handleAddLocal}
        onAddCloudCounterpart={c.handleAddCloudCounterpart}
        onReprobe={c.handleReprobe}
        onConfigureManually={c.handleConfigureManually}
        onRetryPresets={c.onRetryPresets}
      />

      <ProviderFormModal
        open={c.modalOpen}
        onClose={() => c.setModalOpen(false)}
        mode="create"
        initialPreset={c.modalPreset}
        overrides={c.modalOverrides}
      />

      <ProvidersValidationErrors validation={c.validation} />
    </div>
  )
}
