import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from 'react'
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

interface ProviderFormValues {
  name: string
  authType: AuthType
  apiKey: string
  subscriptionToken: string
  baseUrl: string
  litellmProvider: string
  tosAccepted: boolean
}

function buildCreateFromPresetRequest(
  presetName: string,
  v: ProviderFormValues,
): CreateFromPresetRequest {
  return {
    preset_name: presetName,
    name: v.name.trim(),
    auth_type: v.authType,
    api_key: v.authType === 'api_key' && v.apiKey ? v.apiKey : undefined,
    subscription_token:
      v.authType === 'subscription' && v.subscriptionToken ? v.subscriptionToken : undefined,
    tos_accepted: v.authType === 'subscription' && v.tosAccepted,
    base_url: v.baseUrl.trim() || undefined,
  }
}

function buildCreateProviderRequest(v: ProviderFormValues): CreateProviderRequest {
  return {
    name: v.name.trim(),
    driver: 'litellm',
    litellm_provider: v.litellmProvider || undefined,
    auth_type: v.authType,
    api_key: v.authType === 'api_key' && v.apiKey ? v.apiKey : undefined,
    subscription_token:
      v.authType === 'subscription' && v.subscriptionToken ? v.subscriptionToken : undefined,
    tos_accepted: v.authType === 'subscription' && v.tosAccepted,
    base_url: v.baseUrl.trim() || undefined,
    models: [],
  }
}

function buildUpdateProviderRequest(v: ProviderFormValues): UpdateProviderRequest {
  return {
    litellm_provider: v.litellmProvider || undefined,
    auth_type: v.authType,
    api_key: v.authType === 'api_key' && v.apiKey ? v.apiKey : undefined,
    clear_api_key: v.authType !== 'api_key',
    subscription_token:
      v.authType === 'subscription' && v.subscriptionToken ? v.subscriptionToken : undefined,
    clear_subscription_token: v.authType !== 'subscription',
    tos_accepted: v.authType === 'subscription' && v.tosAccepted,
    base_url: v.baseUrl.trim() || undefined,
  }
}

function computeAvailableAuthTypes(
  cloudPreset: CloudPreset | null,
  preset: ProviderPreset | undefined,
): { value: AuthType; label: string }[] {
  if (cloudPreset) {
    return AUTH_OPTIONS.filter((opt) => cloudPreset.supported_auth_types.includes(opt.value))
  }
  if (preset?.kind === 'local') {
    return AUTH_OPTIONS.filter((opt) => opt.value === 'none')
  }
  return AUTH_OPTIONS
}

function computeBaseUrlHint(
  isCustom: boolean,
  mode: 'create' | 'edit',
  preset: ProviderPreset | undefined,
): string | undefined {
  if (isCustom || mode === 'edit') return undefined
  if (preset?.requires_base_url) return 'Required for this provider'
  if (preset) return 'Optional. Override the default endpoint.'
  return undefined
}

function computeShowBillingHint(cloudPreset: CloudPreset | null, authType: AuthType): boolean {
  return (
    cloudPreset != null &&
    cloudPreset.name === 'anthropic' &&
    authType === 'subscription' &&
    cloudPreset.supported_auth_types.includes('subscription')
  )
}

interface ProviderFields {
  selectedPreset: string | null
  setSelectedPreset: Dispatch<SetStateAction<string | null>>
  name: string
  setName: Dispatch<SetStateAction<string>>
  authType: AuthType
  setAuthType: Dispatch<SetStateAction<AuthType>>
  apiKey: string
  setApiKey: Dispatch<SetStateAction<string>>
  subscriptionToken: string
  setSubscriptionToken: Dispatch<SetStateAction<string>>
  baseUrl: string
  setBaseUrl: Dispatch<SetStateAction<string>>
  litellmProvider: string
  setLitellmProvider: Dispatch<SetStateAction<string>>
  submitting: boolean
  setSubmitting: Dispatch<SetStateAction<boolean>>
  showTosDialog: boolean
  setShowTosDialog: Dispatch<SetStateAction<boolean>>
  tosAccepted: boolean
  setTosAccepted: Dispatch<SetStateAction<boolean>>
}

function useProviderFields(): ProviderFields {
  const [selectedPreset, setSelectedPreset] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [authType, setAuthType] = useState<AuthType>('api_key')
  const [apiKey, setApiKey] = useState('')
  const [subscriptionToken, setSubscriptionToken] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [litellmProvider, setLitellmProvider] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [showTosDialog, setShowTosDialog] = useState(false)
  const [tosAccepted, setTosAccepted] = useState(false)
  return {
    selectedPreset,
    setSelectedPreset,
    name,
    setName,
    authType,
    setAuthType,
    apiKey,
    setApiKey,
    subscriptionToken,
    setSubscriptionToken,
    baseUrl,
    setBaseUrl,
    litellmProvider,
    setLitellmProvider,
    submitting,
    setSubmitting,
    showTosDialog,
    setShowTosDialog,
    tosAccepted,
    setTosAccepted,
  }
}

function applyEditModeReset(fields: ProviderFields): void {
  fields.setSelectedPreset(null)
  fields.setApiKey('')
  fields.setSubscriptionToken('')
}

function applyEditPrefill(fields: ProviderFields, provider: ProviderWithName): void {
  fields.setName(provider.name)
  fields.setAuthType(provider.auth_type)
  fields.setBaseUrl(provider.base_url ?? '')
  fields.setLitellmProvider(provider.litellm_provider ?? '')
  fields.setTosAccepted(provider.tos_accepted_at !== null)
}

function applyCustomPresetSync(fields: ProviderFields): void {
  fields.setName('')
  fields.setAuthType('api_key')
  fields.setApiKey('')
  fields.setSubscriptionToken('')
  fields.setBaseUrl('')
  fields.setLitellmProvider('')
  fields.setTosAccepted(false)
}

function applyPresetSync(fields: ProviderFields, preset: ProviderPreset): void {
  fields.setName(preset.name)
  fields.setAuthType(preset.auth_type)
  fields.setBaseUrl(preset.default_base_url ?? '')
  fields.setLitellmProvider(preset.litellm_provider)
  fields.setTosAccepted(false)
  fields.setSubscriptionToken('')
  fields.setApiKey('')
}

interface SyncArgs {
  open: boolean
  mode: 'create' | 'edit'
  provider: ProviderWithName | null | undefined
  initialPreset: string | null
  preset: ProviderPreset | undefined
  fields: ProviderFields
}

interface SyncRefs {
  prevOpen: { current: boolean | undefined }
  prevMode: { current: 'create' | 'edit' | undefined }
  prevProvider: { current: ProviderWithName | null | undefined }
  prevSelectedPreset: { current: string | null | undefined }
}

function wasTransition(args: SyncArgs, refs: SyncRefs): boolean {
  return (
    args.open !== refs.prevOpen.current ||
    args.mode !== refs.prevMode.current ||
    args.provider !== refs.prevProvider.current
  )
}

// We mirror props (open / mode / provider / initialPreset / the chosen
// preset) into local controlled inputs by comparing each prop to its
// prior value via refs and conditionally calling setState during
// render. This is React's documented "Adjusting state when a prop
// changes" pattern. Refs (not state) hold the previous values so the
// comparison itself does not schedule an extra render, and the
// render-phase form (not useEffect) avoids the set-state-in-effect
// anti-pattern plus the StrictMode double-fire. Each setState is
// idempotent under repeat invocation.
function useRenderPhaseSync(args: SyncArgs): void {
  const prevOpen = useRef<boolean | undefined>(undefined)
  const prevMode = useRef<'create' | 'edit' | undefined>(undefined)
  const prevProvider = useRef<ProviderWithName | null | undefined>(undefined)
  const prevSelectedPreset = useRef<string | null | undefined>(undefined)
  const refs: SyncRefs = { prevOpen, prevMode, prevProvider, prevSelectedPreset }

  const { open, mode, provider, initialPreset, preset, fields } = args
  const transition = wasTransition(args, refs)
  const selectedPresetChanged = fields.selectedPreset !== prevSelectedPreset.current

  // 1. Clear credentials when transitioning into edit mode.
  if (open && mode === 'edit' && transition) applyEditModeReset(fields)
  // 2. Seed selected preset from ``initialPreset`` on create-mode open
  //    (``null`` maps to ``__custom__`` so the picker dropdown shows).
  if (open && mode === 'create' && open !== prevOpen.current) {
    fields.setSelectedPreset(initialPreset ?? '__custom__')
  }
  // 3. Pre-fill from the provider when entering edit mode.
  if (open && mode === 'edit' && provider && transition) applyEditPrefill(fields, provider)
  // 4. Sync form fields when the chosen preset changes (or custom mode).
  if (selectedPresetChanged) {
    if (fields.selectedPreset === '__custom__') applyCustomPresetSync(fields)
    else if (preset) applyPresetSync(fields, preset)
  }

  prevMode.current = mode
  prevProvider.current = provider
  prevOpen.current = open
  prevSelectedPreset.current = fields.selectedPreset
}

interface ProviderPresetsResult {
  presets: readonly ProviderPreset[]
  presetsLoading: boolean
  presetsError: string | null
  fetchPresetsFn: () => void
}

function useProviderPresets(overrides?: ProviderFormOverrides): ProviderPresetsResult {
  const storePresets = useProvidersStore((s) => s.presets)
  const storePresetsLoading = useProvidersStore((s) => s.presetsLoading)
  const storePresetsError = useProvidersStore((s) => s.presetsError)
  return {
    presets: overrides ? overrides.presets : storePresets,
    presetsLoading: overrides ? overrides.presetsLoading : storePresetsLoading,
    presetsError: overrides ? overrides.presetsError : storePresetsError,
    fetchPresetsFn: overrides?.onFetchPresets ?? useProvidersStore.getState().fetchPresets,
  }
}

interface SubmitArgs {
  mode: 'create' | 'edit'
  provider: ProviderWithName | null | undefined
  preset: ProviderPreset | undefined
  selectedPreset: string | null
  overrides?: ProviderFormOverrides
}

function useProviderSubmit(args: SubmitArgs) {
  const { mode, provider, preset, selectedPreset, overrides } = args

  const submitCreate = useCallback(
    async (values: ProviderFormValues): Promise<boolean> => {
      if (preset && selectedPreset !== '__custom__') {
        const data = buildCreateFromPresetRequest(preset.name, values)
        const result = overrides
          ? await overrides.onCreateFromPreset(data)
          : await useProvidersStore.getState().createFromPreset(data)
        return result !== null
      }
      const data = buildCreateProviderRequest(values)
      const createFn = overrides?.onCreateProvider ?? useProvidersStore.getState().createProvider
      const result = await createFn(data)
      return result !== null
    },
    [preset, selectedPreset, overrides],
  )

  const submitEdit = useCallback(
    async (values: ProviderFormValues): Promise<boolean> => {
      if (!provider) return false
      const data = buildUpdateProviderRequest(values)
      const updateFn = overrides?.onUpdateProvider ?? useProvidersStore.getState().updateProvider
      const result = await updateFn(provider.name, data)
      return result !== null
    },
    [provider, overrides],
  )

  return useCallback(
    (values: ProviderFormValues): Promise<boolean> =>
      mode === 'create' ? submitCreate(values) : submitEdit(values),
    [mode, submitCreate, submitEdit],
  )
}

interface ProviderFormController {
  fields: ProviderFields
  presetsLoading: boolean
  presetsError: string | null
  presetOptions: { value: string; label: string }[]
  preset: ProviderPreset | undefined
  isCustom: boolean
  cloudPreset: CloudPreset | null
  showSubscriptionBillingHint: boolean
  availableAuthTypes: { value: AuthType; label: string }[]
  baseUrlHint: string | undefined
  dialogTitle: string
  open: boolean
  mode: 'create' | 'edit'
  provider: ProviderWithName | null | undefined
  handleAuthTypeChange: (value: string) => void
  handleClose: () => void
  handleSubmit: () => Promise<void>
  handleOpenChange: (nextOpen: boolean) => void
}

function useProviderFormController(props: ProviderFormModalProps): ProviderFormController {
  const { open, onClose, mode, provider, initialPreset = null, overrides } = props
  const { presets, presetsLoading, presetsError, fetchPresetsFn } = useProviderPresets(overrides)
  const fields = useProviderFields()

  const preset = presets.find((p) => p.name === fields.selectedPreset)
  const isCustom = fields.selectedPreset === '__custom__'
  const cloudPreset = preset?.kind === 'cloud' ? (preset as CloudPreset) : null
  const showSubscriptionBillingHint = computeShowBillingHint(cloudPreset, fields.authType)
  const availableAuthTypes = computeAvailableAuthTypes(cloudPreset, preset)
  const baseUrlHint = computeBaseUrlHint(isCustom, mode, preset)

  const presetOptions = useMemo(
    () => [
      { value: '__custom__', label: 'Custom endpoint' },
      ...presets.map((p) => ({ value: p.name, label: p.display_name })),
    ],
    [presets],
  )

  useEffect(() => {
    if (open && mode === 'create') {
      fetchPresetsFn()
    }
  }, [open, mode, fetchPresetsFn])

  useRenderPhaseSync({ open, mode, provider, initialPreset, preset, fields })

  const runSubmit = useProviderSubmit({ mode, provider, preset, selectedPreset: fields.selectedPreset, overrides })

  const handleAuthTypeChange = useCallback(
    (value: string) => {
      if (!isAuthType(value)) {
        log.warn('Ignoring unknown auth_type value', value)
        return
      }
      fields.setAuthType(value)
      if (value === 'subscription' && !fields.tosAccepted) {
        fields.setShowTosDialog(true)
      }
    },
    [fields],
  )

  const handleClose = useCallback(() => {
    applyCustomPresetSync(fields)
    fields.setSelectedPreset(null)
    fields.setSubmitting(false)
    onClose()
  }, [fields, onClose])

  const handleSubmit = useCallback(async () => {
    fields.setSubmitting(true)
    try {
      const ok = await runSubmit({
        name: fields.name,
        authType: fields.authType,
        apiKey: fields.apiKey,
        subscriptionToken: fields.subscriptionToken,
        baseUrl: fields.baseUrl,
        litellmProvider: fields.litellmProvider,
        tosAccepted: fields.tosAccepted,
      })
      if (ok) handleClose()
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Unknown error'
      log.error('Submit failed:', msg)
    } finally {
      fields.setSubmitting(false)
    }
  }, [fields, runSubmit, handleClose])

  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      if (!nextOpen && fields.submitting) return
      if (!nextOpen) handleClose()
    },
    [handleClose, fields.submitting],
  )

  return {
    fields,
    presetsLoading,
    presetsError,
    presetOptions,
    preset,
    isCustom,
    cloudPreset,
    showSubscriptionBillingHint,
    availableAuthTypes,
    baseUrlHint,
    dialogTitle: mode === 'create' ? 'Add Provider' : `Edit ${provider?.name ?? 'Provider'}`,
    open,
    mode,
    provider,
    handleAuthTypeChange,
    handleClose,
    handleSubmit,
    handleOpenChange,
  }
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

function SubscriptionBillingHint({ cloudPreset }: { cloudPreset: CloudPreset | null }) {
  return (
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
  )
}

function ProviderCredentialFields({ ctrl }: { ctrl: ProviderFormController }) {
  const { fields, mode, provider } = ctrl
  return (
    <>
      {fields.authType === 'api_key' && (
        <InputField
          label="API Key"
          type="password"
          value={fields.apiKey}
          onChange={(e) => fields.setApiKey(e.target.value)}
          placeholder={mode === 'edit' && provider?.has_api_key ? '(unchanged)' : 'sk-...'}
          hint={mode === 'edit' ? 'Leave empty to keep existing key' : undefined}
        />
      )}

      {fields.authType === 'subscription' && (
        <>
          {!fields.tosAccepted && (
            <div className="rounded-md border border-warning/30 bg-warning/5 p-card text-xs text-text-secondary">
              You must accept the Terms of Service warning before using subscription auth.
              <Button
                variant="outline"
                size="sm"
                className="ml-2"
                onClick={() => fields.setShowTosDialog(true)}
              >
                Review & Accept
              </Button>
            </div>
          )}
          {fields.tosAccepted && (
            <InputField
              label="Subscription Token"
              type="password"
              value={fields.subscriptionToken}
              onChange={(e) => fields.setSubscriptionToken(e.target.value)}
              placeholder="sub-token-..."
              hint="Run 'claude setup-token' in your terminal to get this token"
            />
          )}
        </>
      )}
    </>
  )
}

function ProviderEndpointFields({ ctrl }: { ctrl: ProviderFormController }) {
  const { fields, mode, preset, isCustom, baseUrlHint } = ctrl
  return (
    <>
      <InputField
        label="Provider Name"
        value={fields.name}
        onChange={(e) => fields.setName(e.target.value)}
        placeholder="my-provider"
        hint="Lowercase, alphanumeric + hyphens"
        disabled={mode === 'edit'}
      />

      {(isCustom || preset != null || mode === 'edit') && (
        <InputField
          label="Base URL"
          value={fields.baseUrl}
          onChange={(e) => fields.setBaseUrl(e.target.value)}
          placeholder={preset?.default_base_url ?? 'https://api.example.com/v1'}
          hint={baseUrlHint}
        />
      )}

      {(isCustom || mode === 'edit') && (
        <InputField
          label="LiteLLM Provider"
          value={fields.litellmProvider}
          onChange={(e) => fields.setLitellmProvider(e.target.value)}
          placeholder="e.g. my-cloud, my-local..."
          hint="LiteLLM routing identifier for model name prefixing"
        />
      )}
    </>
  )
}

function isSubmitDisabled(ctrl: ProviderFormController): boolean {
  const { fields, preset } = ctrl
  if (fields.submitting || !fields.name.trim()) return true
  if (fields.authType === 'subscription' && !fields.tosAccepted) return true
  return Boolean(preset?.requires_base_url) && !fields.baseUrl.trim()
}

function ProviderFormFooter({ ctrl }: { ctrl: ProviderFormController }) {
  const { fields, mode, handleSubmit } = ctrl
  return (
    <div className="flex justify-end gap-3 pt-2">
      <Dialog.Close
        render={
          <Button variant="outline" disabled={fields.submitting}>
            Cancel
          </Button>
        }
      />
      <Button onClick={handleSubmit} disabled={isSubmitDisabled(ctrl)}>
        {fields.submitting ? 'Saving...' : mode === 'create' ? 'Create Provider' : 'Save Changes'}
      </Button>
    </div>
  )
}

function ProviderConfigForm({ ctrl }: { ctrl: ProviderFormController }) {
  const { availableAuthTypes, fields, showSubscriptionBillingHint, cloudPreset, handleAuthTypeChange } =
    ctrl
  return (
    <>
      <SelectField
        label="Authentication"
        options={availableAuthTypes}
        value={fields.authType}
        onChange={handleAuthTypeChange}
      />

      {showSubscriptionBillingHint && <SubscriptionBillingHint cloudPreset={cloudPreset} />}

      <ProviderCredentialFields ctrl={ctrl} />
      <ProviderEndpointFields ctrl={ctrl} />
      <ProviderFormFooter ctrl={ctrl} />
    </>
  )
}

function ProviderFormBody({ ctrl }: { ctrl: ProviderFormController }) {
  const { presetsError, presetsLoading, presetOptions, isCustom, mode, fields } = ctrl
  return (
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

        {/* Optional preset switcher -- only visible in custom mode so
            users opening "Configure manually" can still adopt a preset
            without going back to the picker. */}
        {mode === 'create' && isCustom && !presetsLoading && (
          <SelectField
            label="Or pick a preset"
            options={presetOptions}
            value={fields.selectedPreset ?? '__custom__'}
            onChange={(v) => fields.setSelectedPreset(v)}
            hint="Switch to a preset to autofill the LiteLLM routing key, base URL, and auth type."
          />
        )}

        {(fields.selectedPreset !== null || mode === 'edit') && <ProviderConfigForm ctrl={ctrl} />}
      </div>
    </div>
  )
}

export function ProviderFormModal(props: ProviderFormModalProps) {
  const ctrl = useProviderFormController(props)
  const { fields } = ctrl

  return (
    <>
      <Dialog.Root open={ctrl.open} onOpenChange={ctrl.handleOpenChange}>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm transition-opacity duration-200 ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0" />
          <Dialog.Popup
            className={cn(
              'fixed top-1/2 left-1/2 z-50 w-full max-w-3xl -translate-x-1/2 -translate-y-1/2',
              'rounded-xl border border-border bg-card shadow-[var(--so-shadow-card-hover)]',
              'transition-[opacity,translate,scale] duration-200 ease-out',
              'data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0',
              'data-[closed]:scale-95 data-[starting-style]:scale-95 data-[ending-style]:scale-95',
              'flex max-h-[85vh] flex-col sm:max-h-[80vh]',
            )}
          >
            {/* Header */}
            <div className="flex items-center justify-between border-b border-border p-card">
              <Dialog.Title className="text-base font-semibold text-foreground">
                {ctrl.dialogTitle}
              </Dialog.Title>
              <Dialog.Description className="sr-only">
                {ctrl.mode === 'create' ? 'Configure a new LLM provider' : 'Update provider settings'}
              </Dialog.Description>
              <Dialog.Close
                render={
                  <Button variant="ghost" size="icon" aria-label="Close" disabled={fields.submitting}>
                    <X className="size-4" />
                  </Button>
                }
              />
            </div>
            <ProviderFormBody ctrl={ctrl} />
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>

      {/* Subscription ToS Dialog */}
      <ConfirmDialog
        open={fields.showTosDialog}
        onOpenChange={fields.setShowTosDialog}
        title="Subscription Authentication"
        description="Using subscription OAuth tokens in third-party applications may not be permitted by the provider's Terms of Service. This feature is provided as-is, with no guarantees of continued availability. You are responsible for ensuring your usage complies with the provider's terms."
        confirmLabel="I Understand & Accept"
        cancelLabel="Cancel"
        onConfirm={() => {
          fields.setTosAccepted(true)
          fields.setShowTosDialog(false)
        }}
      />
    </>
  )
}
