import type { StoreApi } from 'zustand'
import { create } from 'zustand'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getErrorMessage } from '@/utils/errors'
import { asObjectRecord, asObjectRecordArray } from '@/utils/parse'
import { useToastStore } from '@/stores/toast'
import type { SinkInfo, TestSinkResult } from '@/api/types/settings'
import {
  getNamespaceSettings,
  listSinks,
  testSinkConfig,
  updateSetting,
} from '@/api/endpoints/settings'

const log = createLogger('sinks')

interface SinksState {
  sinks: SinkInfo[]
  loading: boolean
  error: string | null
  fetchSinks: () => Promise<void>
  saveSink: (sink: SinkInfo) => Promise<boolean>
  deleteSink: (sink: SinkInfo) => Promise<boolean>
  testConfig: (data: {
    sink_overrides: string
    custom_sinks: string
  }) => Promise<TestSinkResult | null>
}

type SinksSet = StoreApi<SinksState>['setState']
type SinksGet = StoreApi<SinksState>['getState']

function buildOverrideForSink(sink: SinkInfo): Record<string, unknown> {
  const override: Record<string, unknown> = {
    level: sink.level,
    json_format: sink.json_format,
    enabled: sink.enabled,
  }
  if (sink.rotation) {
    override.rotation = {
      strategy: sink.rotation.strategy,
      max_bytes: sink.rotation.max_bytes,
      backup_count: sink.rotation.backup_count,
    }
  }
  if (sink.routing_prefixes.length > 0) {
    override.routing_prefixes = [...sink.routing_prefixes]
  }
  return override
}

async function readNamespaceObjectEntry(
  key: 'sink_overrides',
): Promise<Record<string, unknown>> {
  const settings = await getNamespaceSettings('observability')
  const entry = settings.find((s) => s.definition.key === key)
  if (!entry?.value) return {}
  const parsed: unknown = JSON.parse(entry.value)
  return asObjectRecord(parsed) ?? {}
}

async function readNamespaceArrayEntry(
  key: 'custom_sinks',
): Promise<Record<string, unknown>[]> {
  const settings = await getNamespaceSettings('observability')
  const entry = settings.find((s) => s.definition.key === key)
  if (!entry?.value) return []
  const parsed: unknown = JSON.parse(entry.value)
  return asObjectRecordArray(parsed)
}

async function saveBuiltInSink(sink: SinkInfo): Promise<void> {
  const existingOverrides = await readNamespaceObjectEntry('sink_overrides')
  existingOverrides[sink.identifier] = buildOverrideForSink(sink)
  await updateSetting('observability', 'sink_overrides', {
    value: JSON.stringify(existingOverrides),
  })
}

async function saveCustomSink(sink: SinkInfo): Promise<void> {
  const custom: Record<string, unknown> = {
    file_path: sink.identifier,
    ...buildOverrideForSink(sink),
  }
  if (sink.routing_prefixes.length > 0) {
    custom.routing_prefixes = [...sink.routing_prefixes]
  }
  const existing = await readNamespaceArrayEntry('custom_sinks')
  const merged = existing.filter((s) => s.file_path !== sink.identifier)
  merged.push(custom)
  await updateSetting('observability', 'custom_sinks', {
    value: JSON.stringify(merged),
  })
}

async function deleteBuiltInSink(sink: SinkInfo): Promise<void> {
  const existingOverrides = await readNamespaceObjectEntry('sink_overrides')
  delete existingOverrides[sink.identifier]
  await updateSetting('observability', 'sink_overrides', {
    value: JSON.stringify(existingOverrides),
  })
}

async function deleteCustomSink(sink: SinkInfo): Promise<void> {
  const existing = await readNamespaceArrayEntry('custom_sinks')
  const next = existing.filter((s) => s.file_path !== sink.identifier)
  await updateSetting('observability', 'custom_sinks', {
    value: JSON.stringify(next),
  })
}

async function fetchSinksImpl(set: SinksSet): Promise<void> {
  set({ loading: true, error: null })
  try {
    const sinks = await listSinks()
    set({ sinks, loading: false })
  } catch (err) {
    const message = err instanceof Error
      ? err.message
      : 'Failed to load sinks'
    set({ error: message, loading: false })
  }
}

async function refreshSinksAfterWrite(get: SinksGet): Promise<void> {
  // Post-write refresh MUST NOT revert a committed mutation. A fetch
  // failure here is a "view may be stale" warning, not a save failure.
  // ``fetchSinks`` swallows list-read errors into the store's
  // ``error`` slot rather than rejecting, so we observe failure via
  // the slot instead of relying on a catch (which would never fire).
  await get().fetchSinks()
  const refreshError = get().error
  if (refreshError === null) return
  log.warn(
    'fetchSinks after sink mutation failed',
    sanitizeForLog(refreshError),
  )
  useToastStore.getState().add({
    variant: 'warning',
    title: 'Sink list may be stale',
    description: refreshError,
  })
}

async function saveSinkImpl(
  set: SinksSet,
  get: SinksGet,
  sink: SinkInfo,
): Promise<boolean> {
  const previous = get().sinks
  set({ error: null })
  try {
    if (sink.is_default) {
      await saveBuiltInSink(sink)
    } else {
      await saveCustomSink(sink)
    }
  } catch (err) {
    log.error('Failed to save sink', sanitizeForLog(err))
    set({ sinks: previous, error: getErrorMessage(err) })
    useToastStore.getState().add({
      variant: 'error',
      title: 'Failed to save sink',
      description: getErrorMessage(err),
    })
    return false
  }
  await refreshSinksAfterWrite(get)
  useToastStore.getState().add({ variant: 'success', title: 'Sink saved' })
  return true
}

async function deleteSinkImpl(
  set: SinksSet,
  get: SinksGet,
  sink: SinkInfo,
): Promise<boolean> {
  const previous = get().sinks
  set({ error: null })
  try {
    if (sink.is_default) {
      await deleteBuiltInSink(sink)
    } else {
      await deleteCustomSink(sink)
    }
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
  await refreshSinksAfterWrite(get)
  useToastStore.getState().add({
    variant: 'success',
    title: sink.is_default ? 'Sink overrides cleared' : 'Sink deleted',
  })
  return true
}

export const useSinksStore = create<SinksState>((set, get) => ({
  sinks: [],
  loading: false,
  error: null,

  fetchSinks: () => fetchSinksImpl(set),
  saveSink: (sink) => saveSinkImpl(set, get, sink),
  deleteSink: (sink) => deleteSinkImpl(set, get, sink),

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
