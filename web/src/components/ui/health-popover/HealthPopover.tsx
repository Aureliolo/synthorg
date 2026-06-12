import { Dialog } from '@base-ui/react/dialog'
import { useCallback, useEffect, useRef, useState, type ReactElement } from 'react'
import { getReadiness } from '@/api/endpoints/health'
import { useWebSocketStore } from '@/stores/websocket'
import { createLogger } from '@/lib/logger'
import { cn } from '@/lib/utils'
import { buildFetchedAtLabel, HealthPopoverContent } from './HealthPopoverContent'
import { deriveHealthSubsystemStates } from './derive-subsystem-states'
import type { LoadState } from './health-popover.utils'

const log = createLogger('HealthDialog')

export interface HealthPopoverProps {
  /** The trigger element, cloned into ``Dialog.Trigger render``. */
  children: ReactElement
}

function useFetchHealth(): {
  loadState: LoadState
  nowMs: number
  setNowMs: (now: number) => void
  fetchHealth: () => void
} {
  const [loadState, setLoadState] = useState<LoadState>({ state: 'idle' })
  const [nowMs, setNowMs] = useState(() => Date.now())
  const latestProbeRef = useRef(0)

  const fetchHealth = useCallback(() => {
    setLoadState({ state: 'loading' })
    const probeId = ++latestProbeRef.current
    getReadiness()
      .then((data) => {
        if (probeId !== latestProbeRef.current) return
        const fetchedAt = new Date()
        setLoadState({ state: 'ok', data, fetchedAt })
        setNowMs(fetchedAt.getTime())
      })
      .catch((err: unknown) => {
        if (probeId !== latestProbeRef.current) return
        const fetchedAt = new Date()
        const message = err instanceof Error ? err.message : 'Health probe failed'
        log.warn('Health probe failed', err)
        setLoadState({ state: 'error', message, fetchedAt })
        setNowMs(fetchedAt.getTime())
      })
  }, [])

  return { loadState, nowMs, setNowMs, fetchHealth }
}

/**
 * Shared health-status modal dialog used by both the StatusBar "all
 * systems normal" pill and the Sidebar "Connected" indicator. A fresh
 * ``/health`` snapshot is fetched each time the dialog opens (and on
 * demand via the refresh button), combined with the live WebSocket
 * connection state from ``useWebSocketStore``, and rendered as a
 * centered modal covering ~70% of the viewport at laptop sizes.
 *
 * The trigger is provided by the caller (any existing visual) via the
 * `children` prop; this component handles the Dialog shell, the
 * fetching, and the rendered health-screen content.
 */
export function HealthPopover({ children }: HealthPopoverProps) {
  const [open, setOpen] = useState(false)
  const { loadState, nowMs, setNowMs, fetchHealth } = useFetchHealth()
  const wsConnected = useWebSocketStore((s) => s.connected)
  const wsReconnectExhausted = useWebSocketStore((s) => s.reconnectExhausted)
  const sseFallbackActive = useWebSocketStore((s) => s.sseFallbackActive)
  const states = deriveHealthSubsystemStates(
    loadState,
    wsConnected,
    wsReconnectExhausted,
    sseFallbackActive,
  )

  // Live-updating "X seconds ago" ticker. Starts when the dialog opens,
  // stops when it closes, so we never hold a background timer for a
  // closed modal. 1-second cadence is fine at this scale: the dialog
  // shows at most 4 subsystem cards and a small metadata block.
  useEffect(() => {
    if (!open) return
    const id = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(id)
  }, [open, setNowMs])

  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      setOpen(nextOpen)
      if (nextOpen) fetchHealth()
    },
    [fetchHealth],
  )

  const fetchedAtLabel = buildFetchedAtLabel(loadState, nowMs)

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Trigger render={children} />
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm transition-opacity duration-[var(--so-transition-default)] ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0" />
        <Dialog.Popup
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-full max-w-3xl -translate-x-1/2 -translate-y-1/2',
            'max-h-[85vh] overflow-y-auto',
            'rounded-xl border border-border-bright bg-surface p-card shadow-[var(--so-shadow-card-hover)]',
            'transition-[opacity,translate,scale] duration-[var(--so-transition-default)] ease-out',
            'data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0',
            'data-[closed]:scale-95 data-[starting-style]:scale-95 data-[ending-style]:scale-95',
          )}
        >
          <HealthPopoverContent
            loadState={loadState}
            states={states}
            fetchedAtLabel={fetchedAtLabel}
            onRefresh={fetchHealth}
          />
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
