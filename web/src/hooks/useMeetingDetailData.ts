import { useEffect, useMemo } from 'react'
import { useMeetingsStore } from '@/stores/meetings'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import type { MeetingResponse } from '@/api/types/meetings'
import type { WsChannel } from '@/api/types/websocket'

const DETAIL_CHANNELS = ['meetings'] as const satisfies readonly WsChannel[]

export interface UseMeetingDetailDataReturn {
  meeting: MeetingResponse | null
  loading: boolean
  error: string | null
  wsConnected: boolean
  wsSetupError: string | null
}

export function useMeetingDetailData(meetingId: string): UseMeetingDetailDataReturn {
  const meeting = useMeetingsStore((s) => s.selectedMeeting)
  const loading = useMeetingsStore((s) => s.loadingDetail)
  const error = useMeetingsStore((s) => s.detailError)

  // Fetch on mount / when meetingId changes
  useEffect(() => {
    if (meetingId) {
      void useMeetingsStore.getState().fetchMeeting(meetingId)
    }
  }, [meetingId])

  // WebSocket for live updates (in-progress meetings)
  const bindings: ChannelBinding[] = useMemo(
    () =>
      DETAIL_CHANNELS.map((channel) => ({
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
    meeting,
    loading,
    error,
    wsConnected,
    wsSetupError,
  }
}
