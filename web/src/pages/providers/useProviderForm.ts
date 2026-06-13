import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from 'react'
import { createLogger } from '@/lib/logger'
import { useProvidersStore } from '@/stores/providers'
import type { AuthType, CloudPreset, ProviderPreset } from '@/api/types/providers'
import type { ProviderWithName } from '@/utils/providers'
import {
  buildCreateFromPresetRequest,
  buildCreateProviderRequest,
  buildUpdateProviderRequest,
  computeAvailableAuthTypes,
  computeBaseUrlHint,
  computeShowBillingHint,
  isAuthType,
  type ProviderFormModalProps,
  type ProviderFormOverrides,
  type ProviderFormValues,
} from './provider-form-helpers'

const log = createLogger('providers')

const EMPTY_PRESETS: readonly ProviderPreset[] = []

export interface ProviderFields {
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
  // Memoised so the object identity is stable across renders where no
  // field value changed; this keeps the handler useCallbacks (which
  // depend on `fields`) from re-creating on every parent re-render.
  return useMemo(
    () => ({
      selectedPreset, setSelectedPreset, name, setName, authType, setAuthType,
      apiKey, setApiKey, subscriptionToken, setSubscriptionToken, baseUrl, setBaseUrl,
      litellmProvider, setLitellmProvider, submitting, setSubmitting,
      showTosDialog, setShowTosDialog, tosAccepted, setTosAccepted,
    }),
    [
      selectedPreset, name, authType, apiKey, subscriptionToken, baseUrl,
      litellmProvider, submitting, showTosDialog, tosAccepted,
    ],
  )
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

// We mirror props (open / mode / provider / initialPreset / the chosen
// preset) into local controlled inputs by comparing each prop to its
// prior value via refs and conditionally calling setState during
// render. This is React's documented "Adjusting state when a prop
// changes" pattern. Refs (not state) hold the previous values so the
// comparison itself does not schedule an extra render, and the
// render-phase form (not useEffect) avoids the set-state-in-effect
// anti-pattern plus the StrictMode double-fire. Each setState is
// idempotent under repeat invocation.
function applyEditTransition(args: SyncArgs, transition: boolean): void {
  if (!transition) return
  const { provider, fields } = args
  applyEditModeReset(fields)
  if (provider) applyEditPrefill(fields, provider)
}

function applyTransitionSync(args: SyncArgs, openChanged: boolean, transition: boolean): void {
  const { open, mode, initialPreset, fields } = args
  if (!open) return
  if (mode === 'edit') {
    applyEditTransition(args, transition)
    return
  }
  if (openChanged) {
    fields.setSelectedPreset(initialPreset ?? '__custom__')
  }
}

function syncSelectedPreset(fields: ProviderFields, preset: ProviderPreset | undefined): void {
  if (fields.selectedPreset === '__custom__') applyCustomPresetSync(fields)
  else if (preset) applyPresetSync(fields, preset)
}

function useRenderPhaseSync(args: SyncArgs): void {
  const { open, mode, provider, preset, fields } = args
  const prevOpenRef = useRef<boolean | undefined>(undefined)
  const prevModeRef = useRef<'create' | 'edit' | undefined>(undefined)
  const prevProviderRef = useRef<ProviderWithName | null | undefined>(undefined)
  const prevSelectedPresetRef = useRef<string | null | undefined>(undefined)

  const openChanged = open !== prevOpenRef.current
  const transition =
    openChanged || mode !== prevModeRef.current || provider !== prevProviderRef.current

  applyTransitionSync(args, openChanged, transition)
  if (fields.selectedPreset !== prevSelectedPresetRef.current) {
    syncSelectedPreset(fields, preset)
  }

  prevModeRef.current = mode
  prevProviderRef.current = provider
  prevOpenRef.current = open
  prevSelectedPresetRef.current = fields.selectedPreset
}

interface ProviderPresetsResult {
  presets: readonly ProviderPreset[]
  presetsLoading: boolean
  presetsError: string | null
  fetchPresetsFn: () => void
}

function useProviderPresets(overrides?: ProviderFormOverrides): ProviderPresetsResult {
  // With overrides the store slices are unused, so the selectors return
  // stable constants to avoid re-rendering this modal on unrelated
  // providers-store updates (the setup wizard owns its own preset state).
  const hasOverrides = overrides != null
  const storePresets = useProvidersStore((s) => (hasOverrides ? EMPTY_PRESETS : s.presets))
  const storePresetsLoading = useProvidersStore((s) => (hasOverrides ? false : s.presetsLoading))
  const storePresetsError = useProvidersStore((s) => (hasOverrides ? null : s.presetsError))
  const storeFetchPresets = useProvidersStore((s) => s.fetchPresets)
  return {
    presets: overrides ? overrides.presets : storePresets,
    presetsLoading: overrides ? overrides.presetsLoading : storePresetsLoading,
    presetsError: overrides ? overrides.presetsError : storePresetsError,
    fetchPresetsFn: overrides?.onFetchPresets ?? storeFetchPresets,
  }
}

interface SubmitArgs {
  mode: 'create' | 'edit'
  provider: ProviderWithName | null | undefined
  preset: ProviderPreset | undefined
  selectedPreset: string | null
  overrides?: ProviderFormOverrides | undefined
}

function useProviderSubmit(args: SubmitArgs): (values: ProviderFormValues) => Promise<boolean> {
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

export interface ProviderFormController {
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
  handleSubmit: () => Promise<void>
  handleOpenChange: (nextOpen: boolean) => void
}

function valuesOf(fields: ProviderFields): ProviderFormValues {
  return {
    name: fields.name,
    authType: fields.authType,
    apiKey: fields.apiKey,
    subscriptionToken: fields.subscriptionToken,
    baseUrl: fields.baseUrl,
    litellmProvider: fields.litellmProvider,
    tosAccepted: fields.tosAccepted,
  }
}

export function useProviderFormController(
  props: ProviderFormModalProps,
): ProviderFormController {
  const { open, onClose, mode, provider, initialPreset = null, overrides } = props
  const { presets, presetsLoading, presetsError, fetchPresetsFn } = useProviderPresets(overrides)
  const fields = useProviderFields()

  const preset = presets.find((p) => p.name === fields.selectedPreset)
  const isCustom = fields.selectedPreset === '__custom__'
  const cloudPreset = preset?.kind === 'cloud' ? (preset) : null

  const presetOptions = useMemo(
    () => [
      { value: '__custom__', label: 'Custom endpoint' },
      ...presets.map((p) => ({ value: p.name, label: p.display_name })),
    ],
    [presets],
  )

  useEffect(() => {
    if (open && mode === 'create') fetchPresetsFn()
  }, [open, mode, fetchPresetsFn])

  useRenderPhaseSync({ open, mode, provider, initialPreset, preset, fields })

  const runSubmit = useProviderSubmit({
    mode, provider, preset, selectedPreset: fields.selectedPreset, overrides,
  })

  const handleAuthTypeChange = useCallback(
    (value: string) => {
      if (!isAuthType(value)) {
        log.warn('Ignoring unknown auth_type value', value)
        return
      }
      fields.setAuthType(value)
      if (value === 'subscription' && !fields.tosAccepted) fields.setShowTosDialog(true)
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
      if (await runSubmit(valuesOf(fields))) handleClose()
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
    showSubscriptionBillingHint: computeShowBillingHint(cloudPreset, fields.authType),
    availableAuthTypes: computeAvailableAuthTypes(cloudPreset, preset),
    baseUrlHint: computeBaseUrlHint(isCustom, mode, preset),
    dialogTitle: mode === 'create' ? 'Add Provider' : `Edit ${provider?.name ?? 'Provider'}`,
    open,
    mode,
    provider,
    handleAuthTypeChange,
    handleSubmit,
    handleOpenChange,
  }
}
