import { useCallback, useEffect, useMemo } from 'react'
import { useMeetingsStore, type MeetingsState } from '@/stores/meetings'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { usePolling } from '@/hooks/usePolling'
import type { MeetingResponse } from '@/api/types/meetings'
import type { WsChannel } from '@/api/types/websocket'

const MEETINGS_POLL_INTERVAL = 30_000
const MEETINGS_CHANNELS = ['meetings'] as const satisfies readonly WsChannel[]

export interface UseMeetingsDataReturn {
  meetings: readonly MeetingResponse[]
  total: number
  loading: boolean
  error: string | null
  triggering: boolean
  wsConnected: boolean
  wsSetupError: string | null
  triggerMeeting: MeetingsState['triggerMeeting']
}

export function useMeetingsData(): UseMeetingsDataReturn {
  const meetings = useMeetingsStore((s) => s.meetings)
  const total = useMeetingsStore((s) => s.total)
  const loading = useMeetingsStore((s) => s.loading)
  const error = useMeetingsStore((s) => s.error)
  const triggering = useMeetingsStore((s) => s.triggering)
  const triggerMeeting = useMeetingsStore((s) => s.triggerMeeting)

  // Initial data fetch
  useEffect(() => {
    void useMeetingsStore.getState().fetchMeetings()
  }, [])

  // Lightweight polling for refresh
  const pollFn = useCallback(async () => {
    await useMeetingsStore.getState().fetchMeetings()
  }, [])
  const polling = usePolling(pollFn, MEETINGS_POLL_INTERVAL)

  useEffect(() => {
    polling.start()
    return () => polling.stop()
    // eslint-disable-next-line @eslint-react/exhaustive-deps -- mount-only effect; polling start/stop are stable useCallback refs
  }, [])

  // WebSocket bindings for real-time updates
  const bindings: ChannelBinding[] = useMemo(
    () =>
      MEETINGS_CHANNELS.map((channel) => ({
        channel,
        handler: (event) => {
          useMeetingsStore.getState().handleWsEvent(event)
        },
      })),
    [],
  )

  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({
    bindings,
  })

  return {
    meetings,
    total,
    loading,
    error,
    triggering,
    wsConnected,
    wsSetupError,
    triggerMeeting,
  }
}
