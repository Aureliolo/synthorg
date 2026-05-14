import { useCallback, useEffect, useMemo } from 'react'
import { useSettingsStore } from '@/stores/settings'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { usePolling } from '@/hooks/usePolling'
import type { SettingDefinition, SettingEntry, SettingNamespace } from '@/api/types/settings'
import type { WsChannel } from '@/api/types/websocket'
import { SETTINGS_POLL_INTERVAL } from '@/utils/constants'

const SETTINGS_CHANNELS = ['system'] as const satisfies readonly WsChannel[]

export interface UseSettingsDataReturn {
  schema: SettingDefinition[]
  entries: SettingEntry[]
  loading: boolean
  error: string | null
  saving: boolean
  saveError: string | null
  wsConnected: boolean
  wsSetupError: string | null
  updateSetting: (
    ns: SettingNamespace,
    key: string,
    value: string,
  ) => Promise<SettingEntry | null>
  resetSetting: (ns: SettingNamespace, key: string) => Promise<boolean>
}

export function useSettingsData(): UseSettingsDataReturn {
  const schema = useSettingsStore((s) => s.schema)
  const entries = useSettingsStore((s) => s.entries)
  const loading = useSettingsStore((s) => s.loading)
  const error = useSettingsStore((s) => s.error)
  const savingKeys = useSettingsStore((s) => s.savingKeys)
  const saveError = useSettingsStore((s) => s.saveError)
  const updateSetting = useSettingsStore((s) => s.updateSetting)
  const resetSetting = useSettingsStore((s) => s.resetSetting)

  // Lightweight polling for entries refresh
  const pollFn = useCallback(async () => {
    await useSettingsStore.getState().refreshEntries()
  }, [])
  const polling = usePolling(pollFn, SETTINGS_POLL_INTERVAL)
  const { start: pollingStart, stop: pollingStop } = polling

  // Fetch initial data, then start polling (avoids duplicate getAllSettings)
  useEffect(() => {
    let cancelled = false
    void useSettingsStore.getState().fetchSettingsData().finally(() => {
      if (!cancelled) pollingStart()
    })
    return () => { cancelled = true; pollingStop() }
  }, [pollingStart, pollingStop])

  // WebSocket bindings for real-time updates
  const bindings: ChannelBinding[] = useMemo(
    () =>
      SETTINGS_CHANNELS.map((channel) => ({
        channel,
        handler: (event) => {
          useSettingsStore.getState().updateFromWsEvent(event)
        },
      })),
    [],
  )

  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({
    bindings,
  })

  return {
    schema,
    entries,
    loading,
    error,
    saving: savingKeys.size > 0,
    saveError,
    wsConnected,
    wsSetupError,
    updateSetting,
    resetSetting,
  }
}
