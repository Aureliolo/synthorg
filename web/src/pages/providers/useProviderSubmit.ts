import { useCallback } from 'react'
import { useProvidersStore } from '@/stores/providers'
import type { ProviderWithName } from '@/utils/providers'
import {
  buildCreateFromPresetRequest,
  buildCreateProviderRequest,
  buildUpdateProviderRequest,
  type ProviderFormOverrides,
  type ProviderFormValues,
} from './provider-form-helpers'
import type { ProviderPreset } from '@/api/types/providers'

interface SubmitArgs {
  mode: 'create' | 'edit'
  provider: ProviderWithName | null | undefined
  preset: ProviderPreset | undefined
  selectedPreset: string | null
  overrides?: ProviderFormOverrides | undefined
}

/**
 * Build the create / update request from form values and dispatch it
 * through the store (or the wizard overrides). Returns whether the
 * mutation succeeded so the caller can close the modal.
 */
export function useProviderSubmit(
  args: SubmitArgs,
): (values: ProviderFormValues) => Promise<boolean> {
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
