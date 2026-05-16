import { create } from 'zustand'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getErrorMessage } from '@/utils/errors'
import { asObjectRecord, asObjectRecordArray } from '@/utils/parse'
import { useToastStore } from '@/stores/toast'
import type { SinkInfo, TestSinkResult } from '@/api/types/settings'
import { getNamespaceSettings, listSinks, testSinkConfig, updateSetting } from '@/api/endpoints/settings'

const log = createLogger('sinks')

interface SinksState {
  sinks: SinkInfo[]
  loading: boolean
  error: string | null
  fetchSinks: () => Promise<void>
  /**
   * Create or update a sink. Follows the canonical store error
   * contract: on failure, logs + emits an error toast + returns
   * ``false``. Callers MUST NOT wrap this in try/catch; branch on
   * the sentinel instead.
   */
  saveSink: (sink: SinkInfo) => Promise<boolean>
  /**
   * Delete a sink. Built-in (``is_default``) sinks are NOT removed --
   * they are reset to defaults by clearing their override entry --
   * because the runtime relies on those identifiers always being
   * present. Custom (operator-added) sinks are dropped from the
   * ``custom_sinks`` array entirely.
   *
   * Sentinel-return contract: on failure, logs + emits an error toast
   * + returns ``false``. Callers MUST NOT wrap in try/catch.
   */
  deleteSink: (sink: SinkInfo) => Promise<boolean>
  testConfig: (data: { sink_overrides: string; custom_sinks: string }) => Promise<TestSinkResult | null>
}

function buildOverrideForSink(sink: SinkInfo): Record<string, unknown> {
  const override: Record<string, unknown> = { level: sink.level, json_format: sink.json_format, enabled: sink.enabled }
  if (sink.rotation) {
    override.rotation = { strategy: sink.rotation.strategy, max_bytes: sink.rotation.max_bytes, backup_count: sink.rotation.backup_count }
  }
  if (sink.routing_prefixes.length > 0) {
    override.routing_prefixes = [...sink.routing_prefixes]
  }
  return override
}

export const useSinksStore = create<SinksState>((set, get) => ({
  sinks: [],
  loading: false,
  error: null,

  fetchSinks: async () => {
    set({ loading: true, error: null })
    try {
      const sinks = await listSinks()
      set({ sinks, loading: false })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load sinks'
      set({ error: message, loading: false })
    }
  },

  saveSink: async (sink) => {
    const previous = get().sinks
    set({ error: null })
    try {
      if (sink.is_default) {
        // Merge with existing overrides instead of replacing all
        let existingOverrides: Record<string, unknown> = {}
        const settings = await getNamespaceSettings('observability')
        const overrideEntry = settings.find((s) => s.definition.key === 'sink_overrides')
        if (overrideEntry?.value) {
          const parsed: unknown = JSON.parse(overrideEntry.value)
          const narrowed = asObjectRecord(parsed)
          if (narrowed) existingOverrides = narrowed
        }
        existingOverrides[sink.identifier] = buildOverrideForSink(sink)
        await updateSetting('observability', 'sink_overrides', { value: JSON.stringify(existingOverrides) })
      } else {
        const custom: Record<string, unknown> = { file_path: sink.identifier, ...buildOverrideForSink(sink) }
        if (sink.routing_prefixes.length > 0) {
          custom.routing_prefixes = [...sink.routing_prefixes]
        }
        // Merge with existing custom sinks instead of overwriting
        let existing: Record<string, unknown>[] = []
        const customSettings = await getNamespaceSettings('observability')
        const customEntry = customSettings.find((s) => s.definition.key === 'custom_sinks')
        if (customEntry?.value) {
          const parsed: unknown = JSON.parse(customEntry.value)
          existing = asObjectRecordArray(parsed)
        }
        const merged = existing.filter((s) => s.file_path !== sink.identifier)
        merged.push(custom)
        await updateSetting('observability', 'custom_sinks', { value: JSON.stringify(merged) })
      }
      await get().fetchSinks()
      useToastStore.getState().add({
        variant: 'success',
        title: 'Sink saved',
      })
      return true
    } catch (err) {
      log.error('Failed to save sink', sanitizeForLog(err))
      // Restore previous sinks if fetchSinks already replaced them.
      set({ sinks: previous, error: getErrorMessage(err) })
      useToastStore.getState().add({
        variant: 'error',
        title: 'Failed to save sink',
        description: getErrorMessage(err),
      })
      return false
    }
  },

  deleteSink: async (sink) => {
    const previous = get().sinks
    set({ error: null })
    try {
      if (sink.is_default) {
        let existingOverrides: Record<string, unknown> = {}
        const settings = await getNamespaceSettings('observability')
        const overrideEntry = settings.find((s) => s.definition.key === 'sink_overrides')
        if (overrideEntry?.value) {
          const parsed: unknown = JSON.parse(overrideEntry.value)
          const narrowed = asObjectRecord(parsed)
          if (narrowed) existingOverrides = narrowed
        }
        // For default sinks, deleting means resetting overrides --
        // the identifier itself stays addressable by the runtime.
        delete existingOverrides[sink.identifier]
        await updateSetting('observability', 'sink_overrides', {
          value: JSON.stringify(existingOverrides),
        })
      } else {
        const customSettings = await getNamespaceSettings('observability')
        const customEntry = customSettings.find((s) => s.definition.key === 'custom_sinks')
        let existing: Record<string, unknown>[] = []
        if (customEntry?.value) {
          const parsed: unknown = JSON.parse(customEntry.value)
          existing = asObjectRecordArray(parsed)
        }
        const next = existing.filter((s) => s.file_path !== sink.identifier)
        await updateSetting('observability', 'custom_sinks', {
          value: JSON.stringify(next),
        })
      }
      await get().fetchSinks()
      useToastStore.getState().add({
        variant: sink.is_default ? 'success' : 'success',
        title: sink.is_default ? 'Sink overrides cleared' : 'Sink deleted',
      })
      return true
    } catch (err) {
      log.error('Failed to delete sink', sanitizeForLog(err))
      set({ sinks: previous, error: getErrorMessage(err) })
      useToastStore.getState().add({
        variant: 'error',
        title: 'Failed to delete sink',
        description: getErrorMessage(err),
      })
      return false
    }
  },

  testConfig: async (data) => {
    try {
      return await testSinkConfig(data)
    } catch (err) {
      log.error('Failed to test sink config', sanitizeForLog(err))
      set({ error: getErrorMessage(err) })
      useToastStore.getState().add({
        variant: 'error',
        title: 'Sink test failed',
        description: getErrorMessage(err),
      })
      return null
    }
  },
}))
