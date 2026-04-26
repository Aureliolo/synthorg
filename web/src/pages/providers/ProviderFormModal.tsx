import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Dialog } from '@base-ui/react/dialog'
import { X } from 'lucide-react'
import { createLogger } from '@/lib/logger'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorBanner } from '@/components/ui/error-banner'
import { useProvidersStore } from '@/stores/providers'
import { cn } from '@/lib/utils'
import type {
  AuthType,
  CloudPreset,
  CreateFromPresetRequest,
  CreateProviderRequest,
  ProviderConfig,
  ProviderPreset,
  UpdateProviderRequest,
} from '@/api/types/providers'
import type { ProviderWithName } from '@/utils/providers'

const log = createLogger('providers')

const AUTH_OPTIONS: { value: AuthType; label: string }[] = [
  { value: 'api_key', label: 'API Key' },
  { value: 'subscription', label: 'Subscription (OAuth)' },
  { value: 'none', label: 'None' },
]

const AUTH_TYPE_VALUES: ReadonlySet<AuthType> = new Set([
  'api_key',
  'oauth',
  'custom_header',
  'subscription',
  'none',
])

function isAuthType(value: string): value is AuthType {
  return AUTH_TYPE_VALUES.has(value as AuthType)
}

/** Optional store-override props for using this drawer outside the Settings page. */
export interface ProviderFormOverrides {
  presets: readonly ProviderPreset[]
  presetsLoading: boolean
  presetsError: string | null
  onFetchPresets: () => void
  onCreateFromPreset: (data: CreateFromPresetRequest) => Promise<ProviderConfig | null>
  onCreateProvider?: (data: CreateProviderRequest) => Promise<ProviderConfig | null>
  onUpdateProvider?: (name: string, data: UpdateProviderRequest) => Promise<ProviderConfig | null>
}

interface ProviderFormModalProps {
  open: boolean
  onClose: () => void
  mode: 'create' | 'edit'
  provider?: ProviderWithName | null
  /**
   * When provided in create mode, the modal opens with this preset
   * pre-selected and the form pre-filled.  Pass ``null`` (or omit) to
   * open in custom-endpoint mode with a small "Or pick a preset"
   * dropdown so users with private gateways can still pick a preset.
   */
  initialPreset?: string | null
  /** When provided, uses these callbacks instead of `useProvidersStore`. */
  overrides?: ProviderFormOverrides
}

export function ProviderFormModal({
  open,
  onClose,
  mode,
  provider,
  // ``null`` means "open in custom-endpoint mode"; explicit string
  // means "open with that preset preselected".  Defaulting to ``null``
  // (rather than leaving it ``undefined``) ensures that consumers who
  // omit the prop still land on a valid initial state instead of the
  // blank-modal failure mode caught by CodeRabbit.
  initialPreset = null,
  overrides,
}: ProviderFormModalProps) {
  // Resolve store vs overrides
  const storePresets = useProvidersStore((s) => s.presets)
  const storePresetsLoading = useProvidersStore((s) => s.presetsLoading)
  const storePresetsError = useProvidersStore((s) => s.presetsError)

  const presets = overrides ? overrides.presets : storePresets
  const presetsLoading = overrides ? overrides.presetsLoading : storePresetsLoading
  const presetsError = overrides ? overrides.presetsError : storePresetsError
  const fetchPresetsFn = overrides?.onFetchPresets ?? useProvidersStore.getState().fetchPresets

  // Form state
  const [selectedPreset, setSelectedPreset] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [authType, setAuthType] = useState<AuthType>('api_key')
  const [apiKey, setApiKey] = useState('')
  const [subscriptionToken, setSubscriptionToken] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [litellmProvider, setLitellmProvider] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // ToS dialog
  const [showTosDialog, setShowTosDialog] = useState(false)
  const [tosAccepted, setTosAccepted] = useState(false)

  // Resolve selected preset
  const preset: ProviderPreset | undefined = presets.find((p) => p.name === selectedPreset)
  const isCustom = selectedPreset === '__custom__'

  // Cloud-only narrowing for cloud-specific fields (supported_auth_types).
  const cloudPreset = preset?.kind === 'cloud' ? (preset as CloudPreset) : null

  // Subscription billing context: the banner copy + pricing link below
  // are Anthropic-specific (Pro/Max plan credits).  Gating on the
  // preset name (rather than just supported_auth_types) keeps a future
  // provider with subscription auth from inheriting the wrong copy.
  // When a second subscription-auth provider lands, move the copy +
  // link into preset metadata and gate the banner on the metadata
  // being present.
  const showSubscriptionBillingHint =
    cloudPreset != null &&
    cloudPreset.name === 'anthropic' &&
    authType === 'subscription' &&
    cloudPreset.supported_auth_types.includes('subscription')

  // Preset options for the small "Or pick a preset" dropdown shown only
  // in custom-config mode (no initial preset and user has not picked).
  const presetOptions = useMemo(
    () => [
      { value: '__custom__', label: 'Custom endpoint' },
      ...presets.map((p) => ({ value: p.name, label: p.display_name })),
    ],
    [presets],
  )

  // Fetch presets when dialog opens in create mode
  useEffect(() => {
    if (open && mode === 'create') {
      fetchPresetsFn()
    }
  }, [open, mode, fetchPresetsFn])

  // ── Render-phase state sync ──────────────────────────────────
  // We mirror props (open / mode / provider / initialPreset / the
  // chosen preset) into local controlled inputs by comparing each
  // prop to its prior value via refs and conditionally calling
  // setState during render.  This is React's documented pattern for
  // "Adjusting state when a prop changes":
  //   https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes
  // We use ``useRef`` instead of ``useState`` for the "previous"
  // value so the comparison itself does not schedule an extra
  // render.  Going through ``useEffect`` here would (a) trip the
  // ``@eslint-react/set-state-in-effect`` rule and (b) double-fire
  // under React StrictMode in dev; the render-phase pattern avoids
  // both.  Each setState below is idempotent under repeat invocation.
  const prevProviderRef = useRef<typeof provider | undefined>(undefined)
  const prevModeRef = useRef<typeof mode | undefined>(undefined)
  const prevOpenRef = useRef<typeof open | undefined>(undefined)
  const prevSelectedPresetRef = useRef<typeof selectedPreset | undefined>(undefined)
  const modeChanged = mode !== prevModeRef.current
  const providerChanged = provider !== prevProviderRef.current
  const openChanged = open !== prevOpenRef.current
  const selectedPresetChanged = selectedPreset !== prevSelectedPresetRef.current

  // 1. Clear credentials when transitioning into edit mode.
  if (open && mode === 'edit' && (openChanged || modeChanged || providerChanged)) {
    setSelectedPreset(null)
    setApiKey('')
    setSubscriptionToken('')
  }

  // 2. Seed selected preset from ``initialPreset`` on create-mode
  //    open.  ``null`` (the default) maps to ``__custom__`` so users
  //    who open the modal without a preset land in custom-endpoint
  //    mode with the "Or pick a preset" dropdown visible.
  if (open && mode === 'create' && openChanged) {
    setSelectedPreset(initialPreset ?? '__custom__')
  }

  // 3. Pre-fill from the provider when entering edit mode.
  if (open && mode === 'edit' && provider && (openChanged || modeChanged || providerChanged)) {
    setName(provider.name)
    setAuthType(provider.auth_type)
    setBaseUrl(provider.base_url ?? '')
    setLitellmProvider(provider.litellm_provider ?? '')
    setTosAccepted(provider.tos_accepted_at !== null)
  }

  // 4. Sync form fields when the user picks a different preset
  //    (or switches to custom mode).
  if (selectedPresetChanged) {
    if (selectedPreset === '__custom__') {
      setName('')
      setAuthType('api_key')
      setApiKey('')
      setSubscriptionToken('')
      setBaseUrl('')
      setLitellmProvider('')
      setTosAccepted(false)
    } else if (preset) {
      setName(preset.name)
      setAuthType(preset.auth_type)
      setBaseUrl(preset.default_base_url ?? '')
      setLitellmProvider(preset.litellm_provider)
      setTosAccepted(false)
      setSubscriptionToken('')
      setApiKey('')
    }
  }

  // Update ref snapshots so the next render compares against the
  // values we just observed.
  prevModeRef.current = mode
  prevProviderRef.current = provider
  prevOpenRef.current = open
  prevSelectedPresetRef.current = selectedPreset

  // Derived hints
  const baseUrlHint =
    isCustom || mode === 'edit' ? undefined
    : preset?.requires_base_url ? 'Required for this provider'
    : preset ? 'Optional -- override the default endpoint'
    : undefined

  // Available auth types based on selected preset.  Only cloud presets
  // declare ``supported_auth_types``; local presets always use NONE.
  const availableAuthTypes = (() => {
    if (cloudPreset) {
      return AUTH_OPTIONS.filter((opt) => cloudPreset.supported_auth_types.includes(opt.value))
    }
    if (preset?.kind === 'local') {
      return AUTH_OPTIONS.filter((opt) => opt.value === 'none')
    }
    return AUTH_OPTIONS
  })()

  const handleAuthTypeChange = useCallback((value: string) => {
    if (!isAuthType(value)) {
      log.warn('Ignoring unknown auth_type value', value)
      return
    }
    setAuthType(value)
    if (value === 'subscription' && !tosAccepted) {
      setShowTosDialog(true)
    }
  }, [tosAccepted])

  const resetForm = useCallback(() => {
    setSelectedPreset(null)
    setName('')
    setAuthType('api_key')
    setApiKey('')
    setSubscriptionToken('')
    setBaseUrl('')
    setLitellmProvider('')
    setSubmitting(false)
    setTosAccepted(false)
  }, [])

  // 5. Block 2 above always seeds ``selectedPreset`` on create-mode
  //    open (mapping ``null`` -> ``__custom__``), so we no longer need
  //    a render-phase ``resetForm()`` here -- block 4 (driven by the
  //    selectedPreset transition) handles every field reset.  Keeping
  //    this comment as a tombstone for the previous render-phase
  //    reset, which ran into a setState-batching ordering bug
  //    (CodeRabbit R1) and was removed.

  const handleClose = useCallback(() => {
    resetForm()
    onClose()
  }, [resetForm, onClose])

  const handleSubmit = useCallback(async () => {
    setSubmitting(true)

    try {
      const trimmedBaseUrl = baseUrl.trim() || undefined
      if (mode === 'create') {
        if (preset && selectedPreset !== '__custom__') {
          const data: CreateFromPresetRequest = {
            preset_name: preset.name,
            name: name.trim(),
            auth_type: authType,
            api_key: authType === 'api_key' && apiKey ? apiKey : undefined,
            subscription_token: authType === 'subscription' && subscriptionToken ? subscriptionToken : undefined,
            tos_accepted: authType === 'subscription' && tosAccepted,
            base_url: trimmedBaseUrl,
          }
          const result = overrides
            ? await overrides.onCreateFromPreset(data)
            : await useProvidersStore.getState().createFromPreset(data)
          if (result) handleClose()
        } else {
          const data: CreateProviderRequest = {
            name: name.trim(),
            litellm_provider: litellmProvider || undefined,
            auth_type: authType,
            api_key: authType === 'api_key' && apiKey ? apiKey : undefined,
            subscription_token: authType === 'subscription' && subscriptionToken ? subscriptionToken : undefined,
            tos_accepted: authType === 'subscription' && tosAccepted,
            base_url: trimmedBaseUrl,
          }
          const createFn = overrides?.onCreateProvider ?? useProvidersStore.getState().createProvider
          const result = await createFn(data)
          if (result) handleClose()
        }
      } else if (mode === 'edit' && provider) {
        const data: UpdateProviderRequest = {
          litellm_provider: litellmProvider || undefined,
          auth_type: authType,
          api_key: authType === 'api_key' && apiKey ? apiKey : undefined,
          clear_api_key: authType !== 'api_key',
          subscription_token: authType === 'subscription' && subscriptionToken ? subscriptionToken : undefined,
          clear_subscription_token: authType !== 'subscription',
          tos_accepted: authType === 'subscription' && tosAccepted,
          base_url: trimmedBaseUrl,
        }
        const updateFn = overrides?.onUpdateProvider ?? useProvidersStore.getState().updateProvider
        const result = await updateFn(provider.name, data)
        if (result) handleClose()
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Unknown error'
      log.error('Submit failed:', msg)
    } finally {
      setSubmitting(false)
    }
  }, [mode, preset, selectedPreset, name, authType, apiKey, subscriptionToken, tosAccepted, baseUrl, litellmProvider, provider, handleClose, overrides])

  const handleOpenChange = useCallback((nextOpen: boolean) => {
    if (!nextOpen && submitting) return
    if (!nextOpen) handleClose()
  }, [handleClose, submitting])

  const dialogTitle = mode === 'create' ? 'Add Provider' : `Edit ${provider?.name ?? 'Provider'}`

  return (
    <>
      <Dialog.Root open={open} onOpenChange={handleOpenChange}>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm transition-opacity duration-200 ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0" />
          <Dialog.Popup
            className={cn(
              'fixed top-1/2 left-1/2 z-50 w-full max-w-3xl -translate-x-1/2 -translate-y-1/2',
              'rounded-xl border border-border bg-card shadow-[var(--so-shadow-card-hover)]',
              'transition-[opacity,translate,scale] duration-200 ease-out',
              'data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0',
              'data-[closed]:scale-95 data-[starting-style]:scale-95 data-[ending-style]:scale-95',
              'flex max-h-[85vh] flex-col',
            )}
          >
            {/* Header */}
            <div className="flex items-center justify-between border-b border-border p-card">
              <Dialog.Title className="text-base font-semibold text-foreground">
                {dialogTitle}
              </Dialog.Title>
              <Dialog.Description className="sr-only">
                {mode === 'create' ? 'Configure a new LLM provider' : 'Update provider settings'}
              </Dialog.Description>
              <Dialog.Close
                render={
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Close"
                    disabled={submitting}
                  >
                    <X className="size-4" />
                  </Button>
                }
              />
            </div>
            {/* Content */}
            <div className="flex-1 overflow-y-auto p-card">
              <div className="flex flex-col gap-section-gap">
                {presetsError && (
                  <ErrorBanner
                    variant="inline"
                    severity="error"
                    title="Failed to load provider presets"
                    description={presetsError}
                  />
                )}

                {/* Optional preset switcher -- only visible in custom mode
                    so users opening "Configure manually" can still adopt a
                    preset without going back to the picker. */}
                {mode === 'create' && isCustom && !presetsLoading && (
                  <SelectField
                    label="Or pick a preset"
                    options={presetOptions}
                    value={selectedPreset ?? '__custom__'}
                    onChange={(v) => setSelectedPreset(v)}
                    hint="Switch to a preset to autofill the LiteLLM routing key, base URL, and auth type."
                  />
                )}

                {/* Configuration form (shown when a preset is chosen,
                    custom mode, or edit mode) */}
                {(selectedPreset !== null || mode === 'edit') && (
                  <>
                    {/* Auth type */}
                    <SelectField
                      label="Authentication"
                      options={availableAuthTypes}
                      value={authType}
                      onChange={handleAuthTypeChange}
                    />

                    {/* Subscription billing context -- shown only when the
                        chosen cloud preset supports subscription auth and
                        the user has selected it.  Anthropic Pro/Max plans
                        consume API credits from the subscription rather
                        than charging the API key separately. */}
                    {showSubscriptionBillingHint && (
                      <ErrorBanner
                        variant="inline"
                        severity="info"
                        title="Counts against your subscription credits"
                        description={`API calls made through ${
                          cloudPreset?.display_name ?? 'this provider'
                        } using subscription auth consume your monthly Pro/Max plan credits, not your API billing budget.`}
                        action={
                          <a
                            href="https://www.anthropic.com/pricing"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs font-medium text-accent underline-offset-2 hover:underline"
                          >
                            View pricing
                          </a>
                        }
                      />
                    )}

                    {/* Auth-specific fields */}
                    {authType === 'api_key' && (
                      <InputField
                        label="API Key"
                        type="password"
                        value={apiKey}
                        onChange={(e) => setApiKey(e.target.value)}
                        placeholder={mode === 'edit' && provider?.has_api_key ? '(unchanged)' : 'sk-...'}
                        hint={mode === 'edit' ? 'Leave empty to keep existing key' : undefined}
                      />
                    )}

                    {authType === 'subscription' && (
                      <>
                        {!tosAccepted && (
                          <div className="rounded-md border border-warning/30 bg-warning/5 p-card text-xs text-text-secondary">
                            You must accept the Terms of Service warning before using subscription auth.
                            <Button
                              variant="outline"
                              size="sm"
                              className="ml-2"
                              onClick={() => setShowTosDialog(true)}
                            >
                              Review & Accept
                            </Button>
                          </div>
                        )}
                        {tosAccepted && (
                          <InputField
                            label="Subscription Token"
                            type="password"
                            value={subscriptionToken}
                            onChange={(e) => setSubscriptionToken(e.target.value)}
                            placeholder="sub-token-..."
                            hint="Run 'claude setup-token' in your terminal to get this token"
                          />
                        )}
                      </>
                    )}

                    {/* Provider name */}
                    <InputField
                      label="Provider Name"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="my-provider"
                      hint="Lowercase, alphanumeric + hyphens"
                      disabled={mode === 'edit'}
                    />

                    {/* Base URL */}
                    {(isCustom || preset != null || mode === 'edit') && (
                      <InputField
                        label="Base URL"
                        value={baseUrl}
                        onChange={(e) => setBaseUrl(e.target.value)}
                        placeholder={preset?.default_base_url ?? 'https://api.example.com/v1'}
                        hint={baseUrlHint}
                      />
                    )}

                    {/* LiteLLM Provider (custom only) */}
                    {(isCustom || mode === 'edit') && (
                      <InputField
                        label="LiteLLM Provider"
                        value={litellmProvider}
                        onChange={(e) => setLitellmProvider(e.target.value)}
                        placeholder="e.g. my-cloud, my-local..."
                        hint="LiteLLM routing identifier for model name prefixing"
                      />
                    )}

                    {/* Submit */}
                    <div className="flex justify-end gap-3 pt-2">
                      <Dialog.Close
                        render={
                          <Button variant="outline" disabled={submitting}>
                            Cancel
                          </Button>
                        }
                      />
                      <Button
                        onClick={handleSubmit}
                        disabled={submitting || !name.trim() || (authType === 'subscription' && !tosAccepted) || (preset?.requires_base_url && !baseUrl.trim())}
                      >
                        {submitting ? 'Saving...' : mode === 'create' ? 'Create Provider' : 'Save Changes'}
                      </Button>
                    </div>
                  </>
                )}
              </div>
            </div>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>

      {/* Subscription ToS Dialog */}
      <ConfirmDialog
        open={showTosDialog}
        onOpenChange={setShowTosDialog}
        title="Subscription Authentication"
        description="Using subscription OAuth tokens in third-party applications may not be permitted by the provider's Terms of Service. This feature is provided as-is, with no guarantees of continued availability. You are responsible for ensuring your usage complies with the provider's terms."
        confirmLabel="I Understand & Accept"
        cancelLabel="Cancel"
        onConfirm={() => {
          setTosAccepted(true)
          setShowTosDialog(false)
        }}
      />
    </>
  )
}
