import { useCallback, useEffect, useMemo } from 'react'
import { useApprovalsStore } from '@/stores/approvals'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { usePolling } from '@/hooks/usePolling'
import { useFreshnessGate } from '@/hooks/useFreshnessGate'
import type { WsChannel } from '@/api/types/websocket'

const APPROVAL_FETCH_LIMIT = 200
const APPROVAL_POLL_INTERVAL = 30_000
const APPROVAL_CHANNELS = ['approvals'] as const satisfies readonly WsChannel[]

export interface UsePendingApprovalsCountReturn {
  pendingCount: number
  loading: boolean
}

/**
 * Track the number of PENDING approvals, kept live via poll + WebSocket.
 *
 * Owns a single fetch of the approvals list into the shared store, so the
 * always-mounted sidebar badge is the one fetcher; other surfaces (e.g. the
 * dashboard panel) read the derived count straight off the store without a
 * second request.
 */
export function usePendingApprovalsCount(): UsePendingApprovalsCountReturn {
  // Plan reviews have their own surface (the Plan Review page + its own badge),
  // so they are excluded from the generic approvals inbox count.
  const pendingCount = useApprovalsStore(
    (s) =>
      s.approvals.filter(
        (a) => a.status === 'pending' && a.source !== 'plan_review',
      ).length,
  )
  const loading = useApprovalsStore((s) => s.loading)

  useEffect(() => {
    void useApprovalsStore.getState().fetchApprovals({ limit: APPROVAL_FETCH_LIMIT })
  }, [])

  const { skipIfFresh, markFresh } = useFreshnessGate()
  const pollFn = useCallback(async () => {
    await useApprovalsStore.getState().fetchApprovals({ limit: APPROVAL_FETCH_LIMIT })
  }, [])
  const { start, stop } = usePolling(pollFn, APPROVAL_POLL_INTERVAL, { skipIfFresh })
  useEffect(() => {
    start()
    return () => stop()
  }, [start, stop])

  const bindings: ChannelBinding[] = useMemo(
    () =>
      APPROVAL_CHANNELS.map((channel) => ({
        channel,
        handler: (event) => {
          markFresh()
          useApprovalsStore.getState().handleWsEvent(event)
        },
      })),
    [markFresh],
  )
  useWebSocket({ bindings })

  return { pendingCount, loading }
}
