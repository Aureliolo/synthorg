import { useCallback, useEffect, useState } from 'react'
import { useTunnelData } from '@/hooks/useTunnelData'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { useTunnelStore } from '@/stores/tunnel'
import type { TunnelPhase } from '@/stores/tunnel'
import { getCsrfToken } from '@/utils/csrf'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('TunnelCard')
const TUNNEL_INTRO_ACK_KEY = 'synthorg.tunnel.intro.acknowledged'

const CLIPBOARD_ERROR_DESCRIPTIONS: Record<string, string> = {
  NotAllowedError: 'Clipboard access denied. Use Ctrl/Cmd+C to copy the URL manually.',
  SecurityError: 'Clipboard access blocked by browser security. Copy the URL manually.',
  InvalidStateError: 'Could not copy: the page lost focus. Click anywhere on the page, then try again.',
  AbortError: 'Copy was cancelled. Try again.',
  NotFoundError: 'Clipboard is not available in this context. Copy manually from the Public URL field.',
}

export const PHASE_STATUS: Record<
  TunnelPhase,
  { status: 'active' | 'idle' | 'error' | 'offline'; label: string; pulse: boolean }
> = {
  stopped: { status: 'offline', label: 'Stopped', pulse: false },
  enabling: { status: 'idle', label: 'Starting...', pulse: true },
  on: { status: 'active', label: 'Running', pulse: false },
  disabling: { status: 'idle', label: 'Stopping...', pulse: true },
  error: { status: 'error', label: 'Error', pulse: false },
}

function clipboardErrorDescription(err: unknown): string {
  if (err instanceof DOMException) {
    return (
      CLIPBOARD_ERROR_DESCRIPTIONS[err.name] ??
      'Clipboard error. Copy the URL manually from the Public URL field.'
    )
  }
  return 'Clipboard not available in this context. Copy the URL manually from the Public URL field.'
}

function readIntroAck(): boolean {
  try {
    return window.localStorage.getItem(TUNNEL_INTRO_ACK_KEY) === '1'
  } catch {
    return false
  }
}

function writeIntroAck(): void {
  try {
    window.localStorage.setItem(TUNNEL_INTRO_ACK_KEY, '1')
  } catch (err) {
    log.warn('Failed to persist tunnel intro acknowledgement', sanitizeForLog(err))
  }
}

/**
 * Best-effort tunnel stop on page unload via `fetch` + `keepalive`
 * (NOT `navigator.sendBeacon`, which strips the `X-CSRF-Token` header
 * the backend write-access guard requires -- a CSRF bypass otherwise).
 */
function sendTunnelStop(): void {
  try {
    const base = import.meta.env.VITE_API_BASE_URL ?? ''
    const url = `${base.replace(/\/+$/, '').replace(/\/api\/v1\/?$/, '')}/api/v1/integrations/tunnel/stop`
    const csrfToken = getCsrfToken()
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (csrfToken) headers['X-CSRF-Token'] = csrfToken
    void fetch(url, { method: 'POST', credentials: 'include', keepalive: true, headers }).catch(
      (err: unknown) => log.warn('Tunnel auto-stop fetch rejected', sanitizeForLog(err)),
    )
  } catch (err) {
    log.warn('Tunnel auto-stop failed', sanitizeForLog(err))
  }
}

function useTunnelAutoStopOnUnload(autoStop: boolean, isRunning: boolean): void {
  useEffect(() => {
    if (!autoStop || !isRunning) return
    const handler = () => sendTunnelStop()
    window.addEventListener('pagehide', handler)
    return () => window.removeEventListener('pagehide', handler)
  }, [autoStop, isRunning])
}

export interface TunnelCardState {
  phase: TunnelPhase
  publicUrl: string | null
  error: string | null
  autoStop: boolean
  tokenMissing: boolean
  isRunning: boolean
  isTransitioning: boolean
  status: (typeof PHASE_STATUS)[TunnelPhase]
  introOpen: boolean
  introMode: 'info' | 'enable'
  setIntroOpen: (open: boolean) => void
  openInfo: () => void
  setAutoStop: (value: boolean) => void
  handleToggle: (next: boolean) => Promise<void>
  handleIntroConfirm: () => Promise<void>
  copyUrl: () => Promise<void>
}

export function useTunnelCard(): TunnelCardState {
  const { phase, publicUrl, error, autoStop, hasAuthToken } = useTunnelData()
  const setAutoStop = useTunnelStore((s) => s.setAutoStop)
  const start = useTunnelStore((s) => s.start)
  const stop = useTunnelStore((s) => s.stop)
  const [introOpen, setIntroOpen] = useState(false)
  // Distinguishes the toggle path (confirm starts the tunnel) from the
  // Info button (confirm is a plain close); without it, opening the
  // explainer and confirming would expose the local backend by accident.
  const [introMode, setIntroMode] = useState<'info' | 'enable'>('info')

  const isRunning = phase === 'on'
  const isTransitioning = phase === 'enabling' || phase === 'disabling'
  useTunnelAutoStopOnUnload(autoStop, isRunning)

  const handleToggle = useCallback(
    async (next: boolean) => {
      if (!next) {
        await stop()
        return
      }
      if (!readIntroAck()) {
        setIntroMode('enable')
        setIntroOpen(true)
        return
      }
      await start()
    },
    [stop, start],
  )

  const handleIntroConfirm = useCallback(async () => {
    writeIntroAck()
    await start()
  }, [start])

  const copyUrl = useCallback(async () => {
    if (!publicUrl) return
    try {
      await navigator.clipboard.writeText(publicUrl)
      useToastStore.getState().add({ variant: 'success', title: 'URL copied' })
    } catch (err) {
      log.warn('Failed to copy tunnel URL', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Could not copy URL',
        description: clipboardErrorDescription(err),
      })
    }
  }, [publicUrl])

  const openInfo = useCallback(() => {
    setIntroMode('info')
    setIntroOpen(true)
  }, [])

  return {
    phase,
    publicUrl,
    error,
    autoStop,
    tokenMissing: hasAuthToken === false,
    isRunning,
    isTransitioning,
    status: PHASE_STATUS[phase],
    introOpen,
    introMode,
    setIntroOpen,
    openInfo,
    setAutoStop,
    handleToggle,
    handleIntroConfirm,
    copyUrl,
  }
}
