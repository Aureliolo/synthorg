import type { StoreApi } from 'zustand'
import {
  createFromPreset,
  createProvider as apiCreateProvider,
  discoverModels,
  getProvider,
  listPresets,
  listProviders,
  probeLocal,
  testConnection,
} from '@/api/endpoints/providers'
import { createLogger } from '@/lib/logger'
// ProviderConfig is the dashboard overlay (Omit + add over the wire
// ProviderResponse from dtos.gen), so it stays imported from
// ``@/api/types/providers``; the barrel only carries the wire shape.
import type { ProviderConfig } from '@/api/types/providers'
import type {
  CreateFromPresetRequest,
  CreateProviderRequest,
  ProbePresetResponse,
} from '@/api/types'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { useToastStore } from '@/stores/toast'
import type {
  ProvidersSlice,
  SetupWizardState,
  SliceCreator,
} from './types'

const log = createLogger('setup-wizard:providers')

type WizSet = StoreApi<SetupWizardState>['setState']
type WizGet = StoreApi<SetupWizardState>['getState']

interface ProbeOutcome {
  results: Record<string, ProbePresetResponse>
  errors: Record<string, string>
}

async function runProbeLocal(label: string): Promise<ProbeOutcome | null> {
  try {
    const response = await probeLocal()
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
    if (Object.keys(errors).length > 0) {
      log.warn('runProbeLocal reported per-preset errors', {
        label,
        errors: sanitizeForLog(errors),
      })
    }
    return { results, errors }
  } catch (err) {
    log.error('runProbeLocal failed', {
      label,
      error: sanitizeForLog(getErrorMessage(err)),
    })
    return null
  }
}

async function fetchProvidersImpl(
  set: WizSet,
  get: WizGet,
): Promise<void> {
  set({ providersLoading: true, providersError: null })
  try {
    const providers = await listProviders()
    set({ providers, providersLoading: false })
    get().recomputeAgentsRevalidation()
  } catch (err) {
    log.error('fetchProviders failed:', getErrorMessage(err))
    set({ providersError: getErrorMessage(err), providersLoading: false })
  }
}

async function fetchPresetsImpl(set: WizSet): Promise<void> {
  set({ presetsLoading: true, presetsError: null })
  try {
    const presets = await listPresets()
    set({ presets, presetsLoading: false })
  } catch (err) {
    log.error('fetchPresets failed:', getErrorMessage(err))
    set({ presetsError: getErrorMessage(err), presetsLoading: false })
  }
}

interface PresetAuthCheck {
  ok: boolean
  authType: 'api_key' | 'none' | 'oauth' | 'subscription' | 'custom_header'
  error?: string
}

function checkPresetAuthCompatibility(
  get: WizGet,
  presetName: string,
): PresetAuthCheck {
  const preset = get().presets.find((p) => p.name === presetName)
  const authType = preset?.auth_type ?? 'api_key'
  if (authType === 'api_key' || authType === 'none') {
    return { ok: true, authType }
  }
  return {
    ok: false,
    authType,
    error: `Preset '${presetName}' uses ${authType} authentication, which requires more than an API key. `
      + 'Use the full provider creation flow to supply the required credentials.',
  }
}

interface DiscoveryWarning {
  warning: string
}

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

interface CreateFromPresetArgs {
  presetName: string
  name: string
  apiKey: string | undefined
  baseUrl: string | undefined
}

type CreatePresetOutcome =
  | { ok: true; warning?: string }
  | { ok: false; error: string }

function buildCreatePresetPayload(
  args: CreateFromPresetArgs,
  authType: 'api_key' | 'none',
) {
  return {
    preset_name: args.presetName,
    name: args.name,
    ...(authType === 'api_key' && args.apiKey !== undefined
      ? { api_key: args.apiKey }
      : {}),
    ...(args.baseUrl !== undefined ? { base_url: args.baseUrl } : {}),
    auth_type: authType,
    tos_accepted: false,
  }
}

async function handlePostCreatePresetDiscovery(
  set: WizSet,
  get: WizGet,
  name: string,
  presetName: string,
): Promise<CreatePresetOutcome | null> {
  const discovery = await discoverModelsWithWarning(
    set,
    get,
    name,
    presetName,
  )
  if (!discovery) return null
  set({ providersWarning: discovery.warning })
  useToastStore.getState().add({
    variant: 'success',
    title: `Provider '${name}' created`,
    description: discovery.warning,
  })
  return { ok: true, warning: discovery.warning }
}

async function createProviderFromPresetImpl(
  set: WizSet,
  get: WizGet,
  args: CreateFromPresetArgs,
): Promise<CreatePresetOutcome> {
  set({ providersError: null, providersWarning: null })
  const authCheck = checkPresetAuthCompatibility(get, args.presetName)
  if (!authCheck.ok) {
    const error = authCheck.error ?? 'Preset auth incompatible'
    set({ providersError: error })
    useToastStore.getState().add({
      variant: 'error',
      title: 'Preset requires extra credentials',
      description: error,
    })
    return { ok: false, error }
  }
  try {
    const provider = await createFromPreset(
      buildCreatePresetPayload(args, authCheck.authType as 'api_key' | 'none'),
    )
    set((s) => ({ providers: { ...s.providers, [args.name]: provider } }))
    get().recomputeAgentsRevalidation()
    if (provider.models.length === 0) {
      const outcome = await handlePostCreatePresetDiscovery(
        set,
        get,
        args.name,
        args.presetName,
      )
      if (outcome) return outcome
    }
    useToastStore.getState().add({
      variant: 'success',
      title: `Provider '${args.name}' created`,
    })
    return { ok: true }
  } catch (err) {
    const msg = getErrorMessage(err)
    log.error('createProviderFromPreset failed:', msg)
    set({ providersError: msg })
    useToastStore.getState().add({
      variant: 'error',
      title: 'Failed to create provider',
      description: msg,
    })
    return { ok: false, error: msg }
  }
}

async function discoverModelsFullFlow(
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

async function createProviderFromPresetFullImpl(
  set: WizSet,
  get: WizGet,
  data: CreateFromPresetRequest,
): Promise<ProviderConfig | null> {
  set({ providersError: null, providersWarning: null })
  try {
    const provider = await createFromPreset(data)
    set((s) => ({ providers: { ...s.providers, [data.name]: provider } }))
    get().recomputeAgentsRevalidation()
    if (provider.models.length === 0) {
      const refreshed = await discoverModelsFullFlow(set, get, data)
      if (refreshed) return refreshed
    }
    return provider
  } catch (err) {
    log.error('createProviderFromPresetFull failed:', getErrorMessage(err))
    set({ providersError: getErrorMessage(err) })
    return null
  }
}

async function createProviderCustomImpl(
  set: WizSet,
  get: WizGet,
  data: CreateProviderRequest,
): Promise<ProviderConfig | null> {
  set({ providersError: null })
  try {
    const provider = await apiCreateProvider(data)
    set((s) => ({ providers: { ...s.providers, [data.name]: provider } }))
    get().recomputeAgentsRevalidation()
    return provider
  } catch (err) {
    log.error('createProviderCustom failed:', getErrorMessage(err))
    set({ providersError: getErrorMessage(err) })
    return null
  }
}

async function testProviderConnectionImpl(
  set: WizSet,
  name: string,
) {
  set({ providersError: null })
  try {
    return await testConnection(name)
  } catch (err) {
    log.error('testProviderConnection failed:', getErrorMessage(err))
    set({ providersError: getErrorMessage(err) })
    throw err
  }
}

async function probeLocalProvidersImpl(
  set: WizSet,
  reset: boolean,
  failureMessage: string,
): Promise<void> {
  if (reset) {
    set({
      probeResults: {},
      probeErrors: {},
      probeGlobalError: null,
      probing: true,
    })
  } else {
    set({ probing: true, probeErrors: {}, probeGlobalError: null })
  }
  const outcome = await runProbeLocal(
    reset ? 'reprobeLocalProviders' : 'probeLocalProviders',
  )
  if (outcome === null) {
    set({ probeGlobalError: 'Local provider probe failed', probing: false })
    useToastStore.getState().add({
      variant: 'error',
      title: 'Local provider probe failed',
      description: failureMessage,
    })
    return
  }
  set({
    probeResults: outcome.results,
    probeErrors: outcome.errors,
    probing: false,
  })
}

export const createProvidersSlice: SliceCreator<ProvidersSlice> = (
  set,
  get,
) => ({
  providers: {},
  presets: [],
  presetsLoading: false,
  presetsError: null,
  probeResults: {},
  probeErrors: {},
  probeGlobalError: null,
  probing: false,
  providersLoading: false,
  providersError: null,
  providersWarning: null,

  fetchProviders: () => fetchProvidersImpl(set, get),
  fetchPresets: () => fetchPresetsImpl(set),
  createProviderFromPreset: (presetName, name, apiKey, baseUrl) =>
    createProviderFromPresetImpl(set, get, {
      presetName,
      name,
      apiKey,
      baseUrl,
    }),
  createProviderFromPresetFull: (data) =>
    createProviderFromPresetFullImpl(set, get, data),
  createProviderCustom: (data) => createProviderCustomImpl(set, get, data),
  testProviderConnection: (name) => testProviderConnectionImpl(set, name),
  probeLocalProviders: () =>
    probeLocalProvidersImpl(
      set,
      false,
      'Could not reach the SynthOrg API. Try Re-scan or skip to configure manually.',
    ),
  reprobeLocalProviders: () =>
    probeLocalProvidersImpl(
      set,
      true,
      'Could not reach the SynthOrg API. Check your connection and try again.',
    ),
})
