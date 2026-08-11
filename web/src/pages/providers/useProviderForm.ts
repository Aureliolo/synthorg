import { useCallback, useEffect, useMemo, useRef } from 'react'
import { createLogger } from '@/lib/logger'
import { useProvidersStore } from '@/stores/providers'
import type { AuthType, CloudPreset, ProviderPreset } from '@/api/types/providers'
import type { ProviderWithName } from '@/utils/providers'
import {
  cloudPresetOf,
  computeAvailableAuthTypes,
  computeBaseUrlHint,
  computeProviderValidation,
  computeShowBillingHint,
  isAuthType,
  providerDialogTitle,
  subscriptionTokenHint,
  type ProviderFieldErrors,
  type ProviderFormModalProps,
  type ProviderFormOverrides,
  type ProviderFormValues,
} from './provider-form-helpers'
import {
  applyCustomPresetSync,
  useProviderFields,
  useRenderPhaseSync,
  valuesOf,
  type ProviderFields,
} from './useProviderFormFields'
import { useProviderSubmit } from './useProviderSubmit'

const log = createLogger('providers')

const EMPTY_PRESETS: readonly ProviderPreset[] = []

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
  subscriptionHint: string
  fieldErrors: ProviderFieldErrors
  apiKeyMissing: boolean
  canSubmit: boolean
  submitError: string | null
  dialogTitle: string
  open: boolean
  mode: 'create' | 'edit'
  provider: ProviderWithName | null | undefined
  handleAuthTypeChange: (value: string) => void
  handleSubmit: () => Promise<void>
  handleOpenChange: (nextOpen: boolean) => void
}

interface ProviderFormHandlers {
  handleAuthTypeChange: (value: string) => void
  handleSubmit: () => Promise<void>
  handleOpenChange: (nextOpen: boolean) => void
}

function useProviderFormHandlers(
  fields: ProviderFields,
  runSubmit: (values: ProviderFormValues) => Promise<boolean>,
  onClose: () => void,
): ProviderFormHandlers {
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

  return { handleAuthTypeChange, handleSubmit, handleOpenChange }
}

/** Fire the modal presets fetch at most once per open (storm guard). */
function useModalPresetsFetch(
  open: boolean,
  mode: 'create' | 'edit',
  presetsLoading: boolean,
  presetCount: number,
  fetchPresetsFn: () => void,
): void {
  // Hold the (volatile-identity) fetch fn in a ref so a fresh callback
  // identity from the parent no longer re-fires this effect. The guard plus
  // the store-level idempotency early-return mean the presets endpoint is
  // hit at most once per open, killing the self-feeding request storm.
  const fetchPresetsRef = useRef(fetchPresetsFn)
  // Fire at most once per open: after a FAILED load, presetsLoading flips
  // false with presetCount still 0, which would otherwise re-satisfy the
  // condition and re-fire the fetch on every render, recreating the very
  // storm this guard exists to stop. Reset on close so the next open retries.
  const attemptedRef = useRef(false)
  fetchPresetsRef.current = fetchPresetsFn
  useEffect(() => {
    if (!open || mode !== 'create') {
      attemptedRef.current = false
      return
    }
    if (!attemptedRef.current && !presetsLoading && presetCount === 0) {
      attemptedRef.current = true
      fetchPresetsRef.current()
    }
  }, [open, mode, presetsLoading, presetCount])
}

export function useProviderFormController(
  props: ProviderFormModalProps,
): ProviderFormController {
  const { open, onClose, mode, provider, initialPreset = null, overrides } = props
  const { presets, presetsLoading, presetsError, fetchPresetsFn } = useProviderPresets(overrides)
  const fields = useProviderFields()

  const preset = presets.find((p) => p.name === fields.selectedPreset)
  const isCustom = fields.selectedPreset === '__custom__'
  const cloudPreset = cloudPresetOf(preset)

  const presetOptions = useMemo(
    () => [
      { value: '__custom__', label: 'Custom endpoint' },
      ...presets.map((p) => ({ value: p.name, label: p.display_name })),
    ],
    [presets],
  )

  useModalPresetsFetch(open, mode, presetsLoading, presets.length, fetchPresetsFn)
  useRenderPhaseSync({ open, mode, provider, initialPreset, preset, fields })

  const runSubmit = useProviderSubmit({
    mode, provider, preset, selectedPreset: fields.selectedPreset, overrides,
  })
  const { handleAuthTypeChange, handleSubmit, handleOpenChange } = useProviderFormHandlers(
    fields,
    runSubmit,
    onClose,
  )

  const validation = computeProviderValidation({
    mode,
    values: valuesOf(fields),
    preset,
    submitting: fields.submitting,
  })

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
    subscriptionHint: subscriptionTokenHint(preset?.display_name),
    fieldErrors: validation.fieldErrors,
    apiKeyMissing: validation.apiKeyMissing,
    canSubmit: validation.canSubmit,
    submitError: overrides?.submitError ?? null,
    dialogTitle: providerDialogTitle(mode, provider?.name),
    open,
    mode,
    provider,
    handleAuthTypeChange,
    handleSubmit,
    handleOpenChange,
  }
}
