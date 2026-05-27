import { AlertTriangle, Copy, Info, Radio } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SectionCard } from '@/components/ui/section-card'
import { StatusBadge } from '@/components/ui/status-badge'
import { ToggleField } from '@/components/ui/toggle-field'
import { cn } from '@/lib/utils'
import { type TunnelCardState, useTunnelCard } from './useTunnelCard'

function TunnelHeaderRow({ onInfo }: { onInfo: () => void }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <p className="text-xs text-text-secondary">
        Expose your local webhook endpoint to the public internet for development.
      </p>
      <Button type="button" size="icon" variant="ghost" aria-label="About the webhook tunnel" onClick={onInfo}>
        <Info className="size-4" aria-hidden />
      </Button>
    </div>
  )
}

function TunnelStatusRow({ tunnel }: { tunnel: TunnelCardState }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="flex items-center gap-2">
        <StatusBadge status={tunnel.status.status} label pulse={tunnel.status.pulse} />
        <span className="text-sm text-text-secondary">{tunnel.status.label}</span>
      </div>
      <ToggleField
        label={tunnel.isRunning ? 'Stop tunnel' : 'Start tunnel'}
        checked={tunnel.isRunning}
        onChange={(next) => void tunnel.handleToggle(next)}
        disabled={tunnel.isTransitioning}
      />
    </div>
  )
}

function TunnelTokenNotice({ tokenMissing, isRunning }: { tokenMissing: boolean; isRunning: boolean }) {
  if (!tokenMissing || isRunning) return null
  return (
    <div className="flex items-start gap-2 rounded-md bg-info/10 p-card text-xs text-info" role="note">
      <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
      <p>
        No tunnel auth token detected. The tunnel will run on ngrok&apos;s free tier: random URL on
        every start, short session window, bandwidth-capped. Sign up at{' '}
        <a
          href="https://dashboard.ngrok.com/get-started/your-authtoken"
          target="_blank"
          rel="noreferrer"
          className="underline"
        >
          dashboard.ngrok.com
        </a>{' '}
        and set the configured tunnel auth-token env var on the backend to get a stable URL and higher
        limits.
      </p>
    </div>
  )
}

function TunnelPublicUrl({
  isRunning,
  publicUrl,
  onCopy,
}: {
  isRunning: boolean
  publicUrl: string | null
  onCopy: () => void
}) {
  if (!isRunning || !publicUrl) return null
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-text-secondary">Public URL</span>
      <div className="flex items-center gap-2">
        <code
          className={cn(
            'flex-1 overflow-x-auto rounded-md border border-border bg-surface',
            'px-3 py-2 font-mono text-xs text-foreground',
          )}
        >
          {publicUrl}
        </code>
        <Button type="button" size="icon" variant="ghost" aria-label="Copy public URL" onClick={onCopy}>
          <Copy className="size-4" aria-hidden />
        </Button>
      </div>
    </div>
  )
}

function TunnelRunningNotices({ tunnel }: { tunnel: TunnelCardState }) {
  return (
    <>
      {tunnel.isRunning && (
        <div className="flex items-start gap-2 rounded-md bg-warning/10 p-card text-xs text-warning" role="alert">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <p>Your local server is publicly reachable at the URL above. Stop the tunnel when you are done.</p>
        </div>
      )}
      {tunnel.error && !tunnel.isRunning && (
        <ErrorBanner variant="inline" severity="error" title="Tunnel failed to start" description={tunnel.error} />
      )}
      {tunnel.isRunning && (
        <ToggleField
          label="Auto-stop on dashboard shutdown"
          description="Best-effort: the tunnel will attempt to stop when this tab unloads."
          checked={tunnel.autoStop}
          onChange={tunnel.setAutoStop}
        />
      )}
    </>
  )
}

function TunnelIntroDialog({ tunnel }: { tunnel: TunnelCardState }) {
  const confirmLabel =
    tunnel.introMode === 'enable' && !tunnel.isRunning ? 'I understand, start tunnel' : 'Close'
  return (
    <ConfirmDialog
      open={tunnel.introOpen}
      onOpenChange={tunnel.setIntroOpen}
      title="About the webhook tunnel"
      confirmLabel={confirmLabel}
      cancelLabel="Cancel"
      onConfirm={async () => {
        if (tunnel.introMode !== 'enable' || tunnel.isRunning) return
        await tunnel.handleIntroConfirm()
      }}
    >
      <div className="mt-4 flex flex-col gap-3 text-sm text-foreground">
        <section>
          <h3 className="font-semibold">What it does</h3>
          <p className="mt-1 text-text-secondary">
            Starts an{' '}
            <a href="https://ngrok.com" target="_blank" rel="noreferrer" className="underline">
              ngrok
            </a>{' '}
            tunnel from a public <code className="font-mono">https://*.ngrok.app</code> URL to your local
            backend on port 8000. Anyone with the URL can reach your backend (your auth + CSRF still apply).
          </p>
        </section>
        <section>
          <h3 className="font-semibold">Use it for</h3>
          <ul className="mt-1 list-disc pl-5 text-text-secondary">
            <li>
              Developing inbound webhook integrations (GitHub, Slack, Stripe, etc.) that need a publicly
              reachable URL to POST events back to.
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
              Production ingress, unless you have deliberately chosen ngrok as your edge and configured a
              static domain + edge auth. The default tier exposes a random URL with no extra protection
              beyond SynthOrg&apos;s own auth.
            </li>
            <li>
              Sharing access with anyone outside your team. The URL is world-reachable; revoke it by
              stopping the tunnel.
            </li>
            <li>
              Anything you would not want ngrok&apos;s edge servers to see in plaintext (TLS terminates at
              the ngrok edge and is re-encrypted onward).
            </li>
          </ul>
        </section>
        {tunnel.tokenMissing && (
          <section className="rounded-md bg-info/10 p-card text-xs text-info">
            <strong>Free tier:</strong> no tunnel auth token is configured, so the URL will rotate on every
            start and the session is bandwidth-capped. Set the configured tunnel auth-token env var on the
            backend for a stable URL and higher limits.
          </section>
        )}
      </div>
    </ConfirmDialog>
  )
}

export function TunnelCard() {
  const tunnel = useTunnelCard()
  return (
    <SectionCard title="Webhook tunnel" icon={Radio}>
      <div className="flex flex-col gap-3">
        <TunnelHeaderRow onInfo={tunnel.openInfo} />
        <TunnelStatusRow tunnel={tunnel} />
        <TunnelTokenNotice tokenMissing={tunnel.tokenMissing} isRunning={tunnel.isRunning} />
        <TunnelPublicUrl isRunning={tunnel.isRunning} publicUrl={tunnel.publicUrl} onCopy={() => void tunnel.copyUrl()} />
        <TunnelRunningNotices tunnel={tunnel} />
      </div>
      <TunnelIntroDialog tunnel={tunnel} />
    </SectionCard>
  )
}
