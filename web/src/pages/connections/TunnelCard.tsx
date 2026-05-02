import { useEffect } from 'react'
import { AlertTriangle, Copy, Radio } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SectionCard } from '@/components/ui/section-card'
import { StatusBadge } from '@/components/ui/status-badge'
import { ToggleField } from '@/components/ui/toggle-field'
import { useTunnelData } from '@/hooks/useTunnelData'
import { createLogger } from '@/lib/logger'
import { cn } from '@/lib/utils'
import { useToastStore } from '@/stores/toast'
import { useTunnelStore } from '@/stores/tunnel'
import type { TunnelPhase } from '@/stores/tunnel'
import { getCsrfToken } from '@/utils/csrf'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('TunnelCard')

const PHASE_STATUS: Record<
  TunnelPhase,
  { status: 'active' | 'idle' | 'error' | 'offline'; label: string; pulse: boolean }
> = {
  stopped: { status: 'offline', label: 'Stopped', pulse: false },
  enabling: { status: 'idle', label: 'Starting...', pulse: true },
  on: { status: 'active', label: 'Running', pulse: false },
  disabling: { status: 'idle', label: 'Stopping...', pulse: true },
  error: { status: 'error', label: 'Error', pulse: false },
}

export function TunnelCard() {
  const { phase, publicUrl, error, autoStop } = useTunnelData()
  const setAutoStop = useTunnelStore((s) => s.setAutoStop)
  const start = useTunnelStore((s) => s.start)
  const stop = useTunnelStore((s) => s.stop)

  const isRunning = phase === 'on'
  const isTransitioning = phase === 'enabling' || phase === 'disabling'
  const status = PHASE_STATUS[phase]

  // Best-effort auto-stop on page unload. We intentionally use
  // `fetch` + `keepalive: true` (NOT `navigator.sendBeacon`) so
  // we can attach the `X-CSRF-Token` header that the backend's
  // write-access guard expects. `sendBeacon` silently strips
  // custom headers and would be a CSRF bypass on this endpoint.
  useEffect(() => {
    if (!autoStop || !isRunning) return
    const handler = () => {
      try {
        const base = import.meta.env.VITE_API_BASE_URL ?? ''
        const url = `${base.replace(/\/+$/, '').replace(/\/api\/v1\/?$/, '')}/api/v1/integrations/tunnel/stop`
        const csrfToken = getCsrfToken()
        const headers: Record<string, string> = {
          'Content-Type': 'application/json',
        }
        if (csrfToken) headers['X-CSRF-Token'] = csrfToken
        void fetch(url, {
          method: 'POST',
          credentials: 'include',
          keepalive: true,
          headers,
        }).catch((err: unknown) => {
          log.warn('Tunnel auto-stop fetch rejected', sanitizeForLog(err))
        })
      } catch (err) {
        log.warn('Tunnel auto-stop failed', sanitizeForLog(err))
      }
    }
    window.addEventListener('pagehide', handler)
    return () => window.removeEventListener('pagehide', handler)
  }, [autoStop, isRunning])

  async function handleToggle(next: boolean) {
    if (next) {
      await start()
    } else {
      await stop()
    }
  }

  async function copyUrl() {
    if (!publicUrl) return
    try {
      await navigator.clipboard.writeText(publicUrl)
      useToastStore.getState().add({
        variant: 'success',
        title: 'URL copied',
      })
    } catch (err) {
      log.warn('Failed to copy tunnel URL', sanitizeForLog(err))
      // Most failures are clipboard permissions denied or no Clipboard
      // API in the current document context. Differentiate the two so
      // the user knows whether to grant permission or use Ctrl+C.
      const isPermissionDenied =
        err instanceof DOMException && err.name === 'NotAllowedError'
      useToastStore.getState().add({
        variant: 'error',
        title: 'Could not copy URL',
        description: isPermissionDenied
          ? 'Clipboard access denied. Use Ctrl+C to copy the URL manually.'
          : 'Clipboard API not available in this browser. Copy manually from the Public URL field.',
      })
    }
  }

  return (
    <SectionCard title="Webhook tunnel" icon={Radio}>
      <div className="flex flex-col gap-3">
        <p className="text-xs text-text-secondary">
          Expose your local webhook endpoint to the public internet for
          development.
        </p>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <StatusBadge status={status.status} label pulse={status.pulse} />
            <span className="text-sm text-text-secondary">{status.label}</span>
          </div>
          <ToggleField
            label={isRunning ? 'Stop tunnel' : 'Start tunnel'}
            checked={isRunning}
            onChange={(next) => void handleToggle(next)}
            disabled={isTransitioning}
          />
        </div>

        {isRunning && publicUrl && (
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-text-secondary">
              Public URL
            </span>
            <div className="flex items-center gap-2">
              <code
                className={cn(
                  'flex-1 overflow-x-auto rounded-md border border-border bg-surface',
                  'px-3 py-2 font-mono text-xs text-foreground',
                )}
              >
                {publicUrl}
              </code>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                aria-label="Copy public URL"
                onClick={() => void copyUrl()}
              >
                <Copy className="size-4" aria-hidden />
              </Button>
            </div>
          </div>
        )}

        {isRunning && (
          <div
            className="flex items-start gap-2 rounded-md bg-warning/10 p-card text-xs text-warning"
            role="alert"
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            <p>
              Your local server is publicly reachable at the URL above. Stop
              the tunnel when you are done.
            </p>
          </div>
        )}

        {error && !isRunning && (
          <ErrorBanner
            variant="inline"
            severity="error"
            title="Tunnel failed to start"
            description={error}
          />
        )}

        {isRunning && (
          <ToggleField
            label="Auto-stop on dashboard shutdown"
            description="Best-effort: the tunnel will attempt to stop when this tab unloads."
            checked={autoStop}
            onChange={setAutoStop}
          />
        )}
      </div>
    </SectionCard>
  )
}
