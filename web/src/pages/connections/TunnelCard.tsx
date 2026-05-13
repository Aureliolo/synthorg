import { useEffect, useState } from 'react'
import { AlertTriangle, Copy, Info, Radio } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
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

const TUNNEL_INTRO_ACK_KEY = 'synthorg.tunnel.intro.acknowledged'

const CLIPBOARD_ERROR_DESCRIPTIONS: Record<string, string> = {
  NotAllowedError:
    'Clipboard access denied. Use Ctrl/Cmd+C to copy the URL manually.',
  SecurityError:
    'Clipboard access blocked by browser security. Copy the URL manually.',
  InvalidStateError:
    'The page is not focused. Click the page and try again.',
  AbortError: 'Copy was cancelled. Try again.',
  NotFoundError:
    'Clipboard is not available in this context. Copy manually from the Public URL field.',
}

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
  const { phase, publicUrl, error, autoStop, hasAuthToken } = useTunnelData()
  const setAutoStop = useTunnelStore((s) => s.setAutoStop)
  const start = useTunnelStore((s) => s.start)
  const stop = useTunnelStore((s) => s.stop)
  const [introOpen, setIntroOpen] = useState(false)

  const isRunning = phase === 'on'
  const isTransitioning = phase === 'enabling' || phase === 'disabling'
  const status = PHASE_STATUS[phase]
  const tokenMissing = hasAuthToken === false

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
    if (!next) {
      await stop()
      return
    }
    // First-time enable: show the explainer so operators know what
    // they are opting into (public-internet exposure, dev workflow,
    // when to stop). After the operator acknowledges once we skip
    // the dialog on subsequent toggles in the same browser; localStorage
    // is intentional so the ack survives a refresh but resets if the
    // user clears site data.
    const acknowledged = (() => {
      try {
        return window.localStorage.getItem(TUNNEL_INTRO_ACK_KEY) === '1'
      } catch {
        return false
      }
    })()
    if (!acknowledged) {
      setIntroOpen(true)
      return
    }
    await start()
  }

  async function handleIntroConfirm() {
    try {
      window.localStorage.setItem(TUNNEL_INTRO_ACK_KEY, '1')
    } catch (err) {
      log.warn('Failed to persist tunnel intro acknowledgement', sanitizeForLog(err))
    }
    await start()
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
      // Common DOMException paths each get a specific recovery hint;
      // anything else falls through to the generic copy-manually
      // message. This avoids the misleading "API not available"
      // message when the actual cause is e.g. a focus issue or an
      // aborted operation that the user can simply retry.
      const description =
        err instanceof DOMException
          ? CLIPBOARD_ERROR_DESCRIPTIONS[err.name]
              ?? 'Clipboard error. Copy the URL manually from the Public URL field.'
          : 'Clipboard not available in this context. Copy the URL manually from the Public URL field.'
      useToastStore.getState().add({
        variant: 'error',
        title: 'Could not copy URL',
        description,
      })
    }
  }

  return (
    <SectionCard title="Webhook tunnel" icon={Radio}>
      <div className="flex flex-col gap-3">
        <div className="flex items-start justify-between gap-3">
          <p className="text-xs text-text-secondary">
            Expose your local webhook endpoint to the public internet for
            development.
          </p>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            aria-label="About the webhook tunnel"
            onClick={() => setIntroOpen(true)}
          >
            <Info className="size-4" aria-hidden />
          </Button>
        </div>
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

        {tokenMissing && !isRunning && (
          <div
            className="flex items-start gap-2 rounded-md bg-info/10 p-card text-xs text-info"
            role="note"
          >
            <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
            <p>
              No <code className="font-mono">NGROK_AUTHTOKEN</code> detected.
              The tunnel will run on ngrok&apos;s free tier: random URL on
              every start, short session window, bandwidth-capped. Sign up at{' '}
              <a
                href="https://dashboard.ngrok.com/get-started/your-authtoken"
                target="_blank"
                rel="noreferrer"
                className="underline"
              >
                dashboard.ngrok.com
              </a>{' '}
              and set the env var on the backend to get a stable URL and
              higher limits.
            </p>
          </div>
        )}

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
      <ConfirmDialog
        open={introOpen}
        onOpenChange={setIntroOpen}
        title="About the webhook tunnel"
        confirmLabel={isRunning ? 'Close' : 'I understand, start tunnel'}
        cancelLabel="Cancel"
        onConfirm={async () => {
          if (isRunning) return
          await handleIntroConfirm()
        }}
      >
        <div className="mt-4 flex flex-col gap-3 text-sm text-foreground">
          <section>
            <h3 className="font-semibold">What it does</h3>
            <p className="mt-1 text-text-secondary">
              Starts an{' '}
              <a
                href="https://ngrok.com"
                target="_blank"
                rel="noreferrer"
                className="underline"
              >
                ngrok
              </a>{' '}
              tunnel from a public{' '}
              <code className="font-mono">https://*.ngrok.app</code> URL to
              your local backend on port 8000. Anyone with the URL can reach
              your backend (your auth + CSRF still apply).
            </p>
          </section>
          <section>
            <h3 className="font-semibold">Use it for</h3>
            <ul className="mt-1 list-disc pl-5 text-text-secondary">
              <li>
                Developing inbound webhook integrations (GitHub, Slack,
                Stripe, etc.) that need a publicly reachable URL to POST
                events back to.
              </li>
              <li>
                Letting an MCP client drive the tunnel via{' '}
                <code className="font-mono">synthorg_tunnel_connect</code>.
              </li>
            </ul>
          </section>
          <section>
            <h3 className="font-semibold">Do NOT use it for</h3>
            <ul className="mt-1 list-disc pl-5 text-text-secondary">
              <li>
                Production ingress, unless you have deliberately chosen
                ngrok as your edge and configured a static domain + edge
                auth. The default tier exposes a random URL with no extra
                protection beyond SynthOrg&apos;s own auth.
              </li>
              <li>
                Sharing access with anyone outside your team. The URL is
                world-reachable; revoke it by stopping the tunnel.
              </li>
              <li>
                Anything you would not want ngrok&apos;s edge servers to
                see in plaintext (TLS terminates at the ngrok edge and is
                re-encrypted onward).
              </li>
            </ul>
          </section>
          {tokenMissing && (
            <section className="rounded-md bg-info/10 p-card text-xs text-info">
              <strong>Free tier:</strong> no{' '}
              <code className="font-mono">NGROK_AUTHTOKEN</code> is set, so
              the URL will rotate on every start and the session is
              bandwidth-capped. Set the env var on the backend for a stable
              URL and higher limits.
            </section>
          )}
        </div>
      </ConfirmDialog>
    </SectionCard>
  )
}
