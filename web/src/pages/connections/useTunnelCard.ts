import { useCallback, useEffect, useState } from 'react'
import { useTunnelData } from '@/hooks/useTunnelData'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { useTunnelStore } from '@/stores/tunnel'
import type { TunnelPhase } from '@/stores/tunnel'
import type {
  DeviceLoginPrompt,
  TunnelProviderId,
  TunnelProviderStatus,
} from '@/api/types/integrations'
import { useDashboardPrefs } from '@/stores/dashboard-prefs'
import { getCsrfToken } from '@/utils/csrf'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('TunnelCard')

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
  isRunning: boolean
  isTransitioning: boolean
  status: (typeof PHASE_STATUS)[TunnelPhase]
  providers: readonly TunnelProviderStatus[]
  selectedProvider: string | null
  selectedStatus: TunnelProviderStatus | null
  activeProvider: string | null
  deviceLogin: DeviceLoginPrompt | null
  savingCredential: boolean
  /** Whether Start is actionable for the selected provider. */
  canStart: boolean
  introOpen: boolean
  introMode: 'info' | 'enable'
  setIntroOpen: (open: boolean) => void
  openInfo: () => void
  setAutoStop: (value: boolean) => void
  handleToggle: (next: boolean) => Promise<void>
  handleIntroConfirm: () => Promise<void>
  copyUrl: () => Promise<void>
  selectProvider: (provider: TunnelProviderId) => void
  saveCredential: (token: string) => Promise<boolean>
  clearCredential: () => Promise<boolean>
  connectDevice: () => void
}

interface ProviderActions {
  selectProvider: (provider: TunnelProviderId) => void
  saveCredential: (token: string) => Promise<boolean>
  clearCredential: () => Promise<boolean>
  connectDevice: () => void
}

function useProviderActions(selectedProvider: string | null): ProviderActions {
  const selectProvider = useCallback((provider: TunnelProviderId) => {
    void useTunnelStore.getState().selectProvider(provider)
  }, [])

  const saveCredential = useCallback(
    async (token: string) => {
      if (!selectedProvider) return false
      return useTunnelStore.getState().saveCredential(selectedProvider, token)
    },
    [selectedProvider],
  )

  const clearCredential = useCallback(async () => {
    if (!selectedProvider) return false
    return useTunnelStore.getState().clearCredential(selectedProvider)
  }, [selectedProvider])

  const connectDevice = useCallback(() => {
    if (!selectedProvider) return
    void useTunnelStore.getState().beginDeviceLogin(selectedProvider)
  }, [selectedProvider])

  return { selectProvider, saveCredential, clearCredential, connectDevice }
}

interface IntroState {
  introOpen: boolean
  introMode: 'info' | 'enable'
  setIntroOpen: (open: boolean) => void
  openInfo: () => void
  requestEnable: () => void
}

function useIntroDialog(): IntroState {
  const [introOpen, setIntroOpen] = useState(false)
  // Distinguishes the toggle path (confirm starts the tunnel) from the
  // Info button (confirm is a plain close); without it, opening the
  // explainer and confirming would expose the local backend by accident.
  const [introMode, setIntroMode] = useState<'info' | 'enable'>('info')
  const openInfo = useCallback(() => {
    setIntroMode('info')
    setIntroOpen(true)
  }, [])
  const requestEnable = useCallback(() => {
    setIntroMode('enable')
    setIntroOpen(true)
  }, [])
  return { introOpen, introMode, setIntroOpen, openInfo, requestEnable }
}

function useCopyUrl(publicUrl: string | null): () => Promise<void> {
  return useCallback(async () => {
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
}

export function useTunnelCard(): TunnelCardState {
  const {
    phase,
    publicUrl,
    error,
    autoStop,
    providers,
    selectedProvider,
    activeProvider,
    deviceLogin,
  } = useTunnelData()
  const setAutoStop = useTunnelStore((s) => s.setAutoStop)
  const start = useTunnelStore((s) => s.start)
  const stop = useTunnelStore((s) => s.stop)
  const savingCredential = useTunnelStore((s) => s.savingCredential)
  const intro = useIntroDialog()
  const actions = useProviderActions(selectedProvider)

  const isRunning = phase === 'on'
  const isTransitioning = phase === 'enabling' || phase === 'disabling'
  useTunnelAutoStopOnUnload(autoStop, isRunning)

  const selectedStatus =
    providers.find((p) => p.provider_id === selectedProvider) ?? null
  const canStart =
    selectedStatus !== null &&
    selectedStatus.available &&
    selectedStatus.credential_configured

  const handleToggle = useCallback(
    async (next: boolean) => {
      if (!next) {
        await stop()
        return
      }
      if (!useDashboardPrefs.getState().tunnelIntroAcknowledged) {
        intro.requestEnable()
        return
      }
      await start()
    },
    [stop, start, intro],
  )

  const handleIntroConfirm = useCallback(async () => {
    useDashboardPrefs.getState().acknowledgeTunnelIntro()
    await start()
  }, [start])

  const copyUrl = useCopyUrl(publicUrl)

  return {
    phase,
    publicUrl,
    error,
    autoStop,
    isRunning,
    isTransitioning,
    status: PHASE_STATUS[phase],
    providers,
    selectedProvider,
    selectedStatus,
    activeProvider,
    deviceLogin,
    savingCredential,
    canStart,
    introOpen: intro.introOpen,
    introMode: intro.introMode,
    setIntroOpen: intro.setIntroOpen,
    openInfo: intro.openInfo,
    setAutoStop,
    handleToggle,
    handleIntroConfirm,
    copyUrl,
    ...actions,
  }
}
