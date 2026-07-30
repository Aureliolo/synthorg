import { Dialog } from '@base-ui/react/dialog'
import { useCallback, useEffect, useState, type ReactElement } from 'react'
import { useHealthStore } from '@/stores/health'
import { useWebSocketStore } from '@/stores/websocket'
import { cn } from '@/lib/utils'
import { buildFetchedAtLabel, HealthPopoverContent } from './HealthPopoverContent'
import { deriveHealthSubsystemStates } from './derive-subsystem-states'

export interface HealthPopoverProps {
  /** The trigger element, cloned into ``Dialog.Trigger render``. */
  children: ReactElement
}

/**
 * Shared health-status modal dialog used by both the StatusBar status pill and
 * the Sidebar "Connected" indicator. It renders the snapshot in
 * ``useHealthStore`` combined with the live WebSocket connection state from
 * ``useWebSocketStore``, as a centered modal covering ~70% of the viewport at
 * laptop sizes.
 *
 * Reading the shared snapshot rather than fetching its own is what keeps the
 * dialog and the pill that opens it from reporting different verdicts; opening
 * refreshes it, so the pill is up to date the moment the operator looks.
 *
 * The trigger is provided by the caller (any existing visual) via the
 * `children` prop; this component handles the Dialog shell and the rendered
 * health-screen content.
 */
export function HealthPopover({ children }: HealthPopoverProps) {
  const [open, setOpen] = useState(false)
  // Local, not store state: the "x ago" wall clock only ticks while this dialog
  // is open and nothing else renders it, so keeping it in the shared store
  // would re-render every health consumer once a second for a label none of
  // them show.
  const [nowMs, setNowMs] = useState(() => Date.now())
  const loadState = useHealthStore((s) => s.loadState)
  const fetchHealth = useHealthStore((s) => s.fetchHealth)
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
  // shows a handful of subsystem cards and a small metadata block.
  useEffect(() => {
    if (!open) return
    const id = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(id)
  }, [open])

  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      setOpen(nextOpen)
      // Re-read the clock here rather than in the ticker effect: opening is an
      // event, and the value left behind by a previous open can be minutes old,
      // which would show a stale "x ago" until the first tick landed.
      if (!nextOpen) return
      setNowMs(Date.now())
      // Fire and forget: the snapshot renders from the store, and the probe
      // never rejects, so there is nothing to await or handle here.
      void fetchHealth()
    },
    [fetchHealth],
  )

  const refresh = useCallback(() => void fetchHealth(), [fetchHealth])

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
            onRefresh={refresh}
          />
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
