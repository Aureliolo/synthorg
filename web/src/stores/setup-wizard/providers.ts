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
import type { ProbePresetResponse } from '@/api/types/providers'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { useToastStore } from '@/stores/toast'
import type { ProvidersSlice, SliceCreator } from './types'

const log = createLogger('setup-wizard:providers')

interface ProbeOutcome {
  results: Record<string, ProbePresetResponse>
  errors: Record<string, string>
}

async function runProbeLocal(label: string): Promise<ProbeOutcome | null> {
  try {
    const response = await probeLocal()
    // Filter out undefined entries from the envelope
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

export const createProvidersSlice: SliceCreator<ProvidersSlice> = (set, get) => ({
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

  async fetchProviders() {
    set({ providersLoading: true, providersError: null })
    try {
      const providers = await listProviders()
      set({ providers, providersLoading: false })
    } catch (err) {
      log.error('fetchProviders failed:', getErrorMessage(err))
      set({ providersError: getErrorMessage(err), providersLoading: false })
    }
  },

  async fetchPresets() {
    set({ presetsLoading: true, presetsError: null })
    try {
      const presets = await listPresets()
      set({ presets, presetsLoading: false })
    } catch (err) {
      log.error('fetchPresets failed:', getErrorMessage(err))
      set({ presetsError: getErrorMessage(err), presetsLoading: false })
    }
  },

  async createProviderFromPreset(presetName, name, apiKey, baseUrl) {
    set({ providersError: null, providersWarning: null })
    // Source auth_type from the preset metadata. Hardcoding 'api_key'
    // silently coerces oauth / subscription / custom_header / none
    // presets onto the wrong auth path.
    const preset = get().presets.find((p) => p.name === presetName)
    const authType = preset?.auth_type ?? 'api_key'
    // This shortcut only carries api_key + base_url; non-api_key auth
    // (subscription, custom_header, oauth) needs richer credentials
    // that this signature cannot supply. Reject with a clear message
    // pointing the caller at createProviderFromPresetFull rather than
    // letting the server-side validation fail with a confusing error.
    if (authType !== 'api_key' && authType !== 'none') {
      const msg =
        `Preset '${presetName}' uses ${authType} authentication, which requires more than an API key. ` +
        `Use the full provider creation flow to supply the required credentials.`
      set({ providersError: msg })
      useToastStore.getState().add({
        variant: 'error',
        title: 'Preset requires extra credentials',
        description: msg,
      })
      return { ok: false, error: msg }
    }
    try {
      // Only attach ``api_key`` when this preset actually authenticates
      // with one. ``auth_type: 'none'`` (local providers like Ollama)
      // would otherwise carry the api_key field through to the backend,
      // and a non-empty string there is rejected by ``_check_api_key``
      // (CreateFromPresetRequest validator).
      const provider = await createFromPreset({
        preset_name: presetName,
        name,
        ...(authType === 'api_key' ? { api_key: apiKey } : {}),
        base_url: baseUrl,
        auth_type: authType,
        tos_accepted: false,
      })
      set((s) => ({ providers: { ...s.providers, [name]: provider } }))

      if (provider.models.length === 0) {
        try {
          await discoverModels(name, presetName)
          const refreshed = await getProvider(name)
          set((s) => ({ providers: { ...s.providers, [name]: refreshed } }))
          if (refreshed.models.length === 0) {
            // Provider created OK; only model discovery returned empty.
            // Surface as warning (separate slot from providersError) so
            // the caller renders a non-error affordance.
            const warning =
              `Provider '${name}' was created, but no models were discovered. Ensure the provider is running with models available, then refresh the providers list.`
            set({ providersWarning: warning })
            useToastStore.getState().add({
              variant: 'success',
              title: `Provider '${name}' created`,
              description: warning,
            })
            return { ok: true, warning }
          }
        } catch (discoveryErr) {
          const msg = getErrorMessage(discoveryErr)
          log.warn('Model discovery failed', {
            provider: sanitizeForLog(name),
            error: sanitizeForLog(msg),
          })
          const warning =
            `Provider '${name}' was created, but model discovery failed: ${msg}. Ensure the provider is running, then refresh the providers list.`
          set({ providersWarning: warning })
          useToastStore.getState().add({
            variant: 'success',
            title: `Provider '${name}' created`,
            description: warning,
          })
          return { ok: true, warning }
        }
      }
      useToastStore.getState().add({
        variant: 'success',
        title: `Provider '${name}' created`,
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
  },

  async createProviderFromPresetFull(data) {
    set({ providersError: null, providersWarning: null })
    try {
      const provider = await createFromPreset(data)
      set((s) => ({ providers: { ...s.providers, [data.name]: provider } }))

      if (provider.models.length === 0) {
        try {
          await discoverModels(data.name, data.preset_name)
          const refreshed = await getProvider(data.name)
          set((s) => ({ providers: { ...s.providers, [data.name]: refreshed } }))
          if (refreshed.models.length === 0) {
            set({
              providersWarning:
                `Provider '${data.name}' was created, but no models were discovered. Ensure the provider is running with models available, then refresh.`,
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
            providersWarning:
              `Provider '${data.name}' was created, but model discovery failed: ${msg}. Ensure the provider is running, then refresh.`,
          })
        }
      }
      return provider
    } catch (err) {
      log.error('createProviderFromPresetFull failed:', getErrorMessage(err))
      set({ providersError: getErrorMessage(err) })
      return null
    }
  },

  async createProviderCustom(data) {
    set({ providersError: null })
    try {
      const provider = await apiCreateProvider(data)
      set((s) => ({ providers: { ...s.providers, [data.name]: provider } }))
      return provider
    } catch (err) {
      log.error('createProviderCustom failed:', getErrorMessage(err))
      set({ providersError: getErrorMessage(err) })
      return null
    }
  },

  async testProviderConnection(name) {
    set({ providersError: null })
    try {
      return await testConnection(name)
    } catch (err) {
      log.error('testProviderConnection failed:', getErrorMessage(err))
      set({ providersError: getErrorMessage(err) })
      throw err
    }
  },

  async probeLocalProviders() {
    set({ probing: true, probeErrors: {}, probeGlobalError: null })
    const outcome = await runProbeLocal('probeLocalProviders')
    if (outcome === null) {
      set({ probeGlobalError: 'Local provider probe failed', probing: false })
      useToastStore.getState().add({
        variant: 'error',
        title: 'Local provider probe failed',
        description: 'Could not reach the SynthOrg API. Try Re-scan or skip to configure manually.',
      })
      return
    }
    set({ probeResults: outcome.results, probeErrors: outcome.errors, probing: false })
  },

  async reprobeLocalProviders() {
    set({ probeResults: {}, probeErrors: {}, probeGlobalError: null, probing: true })
    const outcome = await runProbeLocal('reprobeLocalProviders')
    if (outcome === null) {
      set({ probeGlobalError: 'Local provider probe failed', probing: false })
      useToastStore.getState().add({
        variant: 'error',
        title: 'Local provider probe failed',
        description: 'Could not reach the SynthOrg API. Check your connection and try again.',
      })
      return
    }
    set({ probeResults: outcome.results, probeErrors: outcome.errors, probing: false })
  },
})
