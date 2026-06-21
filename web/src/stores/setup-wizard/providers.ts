import type { StoreApi } from 'zustand'
import {
  createFromPreset,
  createProvider as apiCreateProvider,
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
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { useToastStore } from '@/stores/toast'
import type {
  ProvidersSlice,
  SetupWizardState,
  SliceCreator,
} from './types'
import {
  discoverModelsFullFlow,
  handlePostCreatePresetDiscovery,
  type CreatePresetOutcome,
} from './providers-discovery'

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
    const { results, errors } = response
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
    set({ providers, providersLoading: false, providersFetched: true })
    get().recomputeAgentsRevalidation()
  } catch (err) {
    log.error('fetchProviders failed:', getErrorMessage(err))
    set({ providersError: getErrorMessage(err), providersLoading: false })
  }
}

// Idempotent: a second call while presets are already loaded (or a load
// is in flight) returns immediately. This is the store-level guard that
// stops the modal fetch effect from self-feeding a request storm even if
// a caller re-fires it; the modal/component guards are defence in depth.
async function fetchPresetsImpl(set: WizSet, get: WizGet): Promise<void> {
  if (get().presets.length > 0 || get().presetsLoading) return
  set({ presetsLoading: true, presetsError: null })
  try {
    const presets = await listPresets()
    set({ presets, presetsLoading: false, presetsFetched: true })
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

interface CreateFromPresetArgs {
  presetName: string
  name: string
  apiKey: string | undefined
  baseUrl: string | undefined
}

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

async function createProviderFromPresetImpl(
  set: WizSet,
  get: WizGet,
  args: CreateFromPresetArgs,
): Promise<CreatePresetOutcome> {
  set({ providersMutationError: null, providersWarning: null })
  const authCheck = checkPresetAuthCompatibility(get, args.presetName)
  if (!authCheck.ok) {
    const error = authCheck.error ?? 'Preset auth incompatible'
    set({ providersMutationError: error })
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
    set({ providersMutationError: msg })
    useToastStore.getState().add({
      variant: 'error',
      title: 'Failed to create provider',
      description: msg,
    })
    return { ok: false, error: msg }
  }
}

async function createProviderFromPresetFullImpl(
  set: WizSet,
  get: WizGet,
  data: CreateFromPresetRequest,
): Promise<ProviderConfig | null> {
  set({ providersMutationError: null, providersWarning: null })
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
    const msg = getErrorMessage(err)
    log.error('createProviderFromPresetFull failed:', msg)
    set({ providersMutationError: msg })
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to create provider'),
      description: msg,
    })
    return null
  }
}

async function createProviderCustomImpl(
  set: WizSet,
  get: WizGet,
  data: CreateProviderRequest,
): Promise<ProviderConfig | null> {
  set({ providersMutationError: null })
  try {
    const provider = await apiCreateProvider(data)
    set((s) => ({ providers: { ...s.providers, [data.name]: provider } }))
    get().recomputeAgentsRevalidation()
    return provider
  } catch (err) {
    const msg = getErrorMessage(err)
    log.error('createProviderCustom failed:', msg)
    set({ providersMutationError: msg })
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to create provider'),
      description: msg,
    })
    return null
  }
}

async function testProviderConnectionImpl(
  set: WizSet,
  name: string,
) {
  set({ providersMutationError: null })
  try {
    const result = await testConnection(name)
    useToastStore.getState().add(
      result.success
        ? { variant: 'success', title: `Connection to '${name}' succeeded` }
        : {
            variant: 'error',
            title: `Connection to '${name}' failed`,
            description: result.error ?? undefined,
          },
    )
    return result
  } catch (err) {
    const msg = getErrorMessage(err)
    log.error('testProviderConnection failed:', msg)
    set({ providersMutationError: msg })
    useToastStore.getState().add({
      variant: 'error',
      title: `Could not test connection to '${name}'`,
      description: msg,
    })
    // The store owns the error UX (state + toast); callers must not wrap
    // mutations in try/catch, so return the failure sentinel instead of
    // rethrowing and leaking error ownership / unhandled rejections.
    return { success: false, error: msg, latency_ms: null, model_tested: null }
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
      probeAttempted: true,
    })
  } else {
    set({ probing: true, probeErrors: {}, probeGlobalError: null, probeAttempted: true })
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
  presetsFetched: false,
  providersFetched: false,
  probeAttempted: false,
  probeResults: {},
  probeErrors: {},
  probeGlobalError: null,
  probing: false,
  providersLoading: false,
  providersError: null,
  providersMutationError: null,
  providersWarning: null,

  fetchProviders: () => fetchProvidersImpl(set, get),
  fetchPresets: () => fetchPresetsImpl(set, get),
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
