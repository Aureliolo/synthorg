import type { StoreApi } from 'zustand'
import { discoverModels, getProvider } from '@/api/endpoints/providers'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { useToastStore } from '@/stores/toast'
import type { ProviderConfig } from '@/api/types/providers'
import type { CreateFromPresetRequest } from '@/api/types'
import type { SetupWizardState } from './types'

const log = createLogger('setup-wizard:providers')

type WizSet = StoreApi<SetupWizardState>['setState']
type WizGet = StoreApi<SetupWizardState>['getState']

export type CreatePresetOutcome =
  | { ok: true; warning?: string }
  | { ok: false; error: string }

interface DiscoveryWarning {
  warning: string
}

/** Discover models for a freshly-created preset provider, returning a soft warning. */
async function discoverModelsWithWarning(
  set: WizSet,
  get: WizGet,
  name: string,
  presetName: string,
): Promise<DiscoveryWarning | null> {
  try {
    await discoverModels(name, presetName)
    const refreshed = await getProvider(name)
    set((s) => ({ providers: { ...s.providers, [name]: refreshed } }))
    get().recomputeAgentsRevalidation()
    if (refreshed.models.length === 0) {
      return {
        warning: `Provider '${name}' was created, but no models were discovered. Ensure the provider is running with models available, then refresh the providers list.`,
      }
    }
    return null
  } catch (discoveryErr) {
    const msg = getErrorMessage(discoveryErr)
    log.warn('Model discovery failed', {
      provider: sanitizeForLog(name),
      error: sanitizeForLog(msg),
    })
    return {
      warning: `Provider '${name}' was created, but model discovery failed: ${msg}. Ensure the provider is running, then refresh the providers list.`,
    }
  }
}

/** Post-create discovery for the simple preset path; surfaces a warning toast. */
export async function handlePostCreatePresetDiscovery(
  set: WizSet,
  get: WizGet,
  name: string,
  presetName: string,
): Promise<CreatePresetOutcome | null> {
  const discovery = await discoverModelsWithWarning(set, get, name, presetName)
  if (!discovery) return null
  set({ providersWarning: discovery.warning })
  // The provider WAS created; only model discovery came back empty / failed.
  // Surface as a warning (not success) so the unresolved-models caveat reads
  // honestly rather than as an unqualified "created".
  useToastStore.getState().add({
    variant: 'warning',
    title: `Provider '${name}' created with warnings`,
    description: discovery.warning,
  })
  return { ok: true, warning: discovery.warning }
}

/** Post-create discovery for the full preset path; sets a warning on the store. */
export async function discoverModelsFullFlow(
  set: WizSet,
  get: WizGet,
  data: CreateFromPresetRequest,
): Promise<ProviderConfig | null> {
  try {
    await discoverModels(data.name, data.preset_name)
    const refreshed = await getProvider(data.name)
    set((s) => ({ providers: { ...s.providers, [data.name]: refreshed } }))
    get().recomputeAgentsRevalidation()
    if (refreshed.models.length === 0) {
      set({
        providersWarning: `Provider '${data.name}' was created, but no models were discovered. Ensure the provider is running with models available, then refresh.`,
      })
    }
    return refreshed
  } catch (discoveryErr) {
    const msg = getErrorMessage(discoveryErr)
    log.warn('Model discovery failed', {
      provider: sanitizeForLog(data.name),
      error: sanitizeForLog(msg),
    })
    set({
      providersWarning: `Provider '${data.name}' was created, but model discovery failed: ${msg}. Ensure the provider is running, then refresh.`,
    })
    return null
  }
}
