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
import { useToastStore } from '@/stores/toast'
import type { ProvidersSlice, SliceCreator } from './types'

const log = createLogger('setup-wizard:providers')

interface ProbeOutcome {
  results: Record<string, ProbePresetResponse>
  errors: Record<string, string>
}

/**
 * Hit the batch probe-local endpoint, normalise the envelope into
 * concrete records, and toast on top-level failure.  Per-preset
 * failures land under ``errors`` and are surfaced inline by
 * ``DetectedLocalList``; this helper does not toast for those.
 */
async function runProbeLocal(label: string): Promise<ProbeOutcome | null> {
  try {
    const response = await probeLocal()
    const results: Record<string, ProbePresetResponse> = {}
    for (const [name, value] of Object.entries(response.results)) {
      if (value !== undefined) results[name] = value
    }
    const errors: Record<string, string> = {}
    for (const [name, msg] of Object.entries(response.errors)) {
      if (msg !== undefined) errors[name] = msg
    }
    if (Object.keys(errors).length > 0) {
      log.warn(`${label} reported per-preset errors`, errors)
    }
    return { results, errors }
  } catch (err) {
    log.error(`${label} failed`, getErrorMessage(err))
    return null
  }
}

export const createProvidersSlice: SliceCreator<ProvidersSlice> = (set) => ({
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
    set({ providersError: null })
    try {
      const provider = await createFromPreset({
        preset_name: presetName,
        name,
        api_key: apiKey,
        base_url: baseUrl,
      })
      set((s) => ({ providers: { ...s.providers, [name]: provider } }))

      if (provider.models.length === 0) {
        try {
          await discoverModels(name, presetName)
          const refreshed = await getProvider(name)
          set((s) => ({ providers: { ...s.providers, [name]: refreshed } }))
          if (refreshed.models.length === 0) {
            set({
              providersError:
                `Provider '${name}' created but no models were discovered. ` +
                'Ensure the provider is running with models available, then refresh.',
            })
          }
        } catch (discoveryErr) {
          const msg = getErrorMessage(discoveryErr)
          log.error('Model discovery failed for', name, msg)
          set({
            providersError:
              `Provider '${name}' created but model discovery failed: ${msg}. ` +
              'Ensure the provider is running, then refresh the providers list.',
          })
        }
      }
    } catch (err) {
      log.error('createProviderFromPreset failed:', getErrorMessage(err))
      set({ providersError: getErrorMessage(err) })
      throw err
    }
  },

  async createProviderFromPresetFull(data) {
    set({ providersError: null })
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
              providersError:
                `Provider '${data.name}' created but no models were discovered. ` +
                'Ensure the provider is running with models available, then refresh.',
            })
          }
          return refreshed
        } catch (discoveryErr) {
          const msg = getErrorMessage(discoveryErr)
          log.error('Model discovery failed for', data.name, msg)
          set({
            providersError:
              `Provider '${data.name}' created but model discovery failed: ${msg}. ` +
              'Ensure the provider is running, then refresh.',
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
