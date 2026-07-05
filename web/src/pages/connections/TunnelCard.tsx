import { useState } from 'react'
import { AlertTriangle, Copy, ExternalLink, Info, KeyRound, Radio } from 'lucide-react'
import type { TunnelProviderId, TunnelProviderStatus } from '@/api/types/integrations'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { SectionCard } from '@/components/ui/section-card'
import { SegmentedControl } from '@/components/ui/segmented-control'
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

function TunnelProviderPicker({ tunnel }: { tunnel: TunnelCardState }) {
  if (tunnel.providers.length === 0 || !tunnel.selectedProvider) return null
  return (
    <SegmentedControl
      label="Tunnel provider"
      size="sm"
      options={tunnel.providers.map((p) => ({
        value: p.provider_id,
        label: p.display_name,
      }))}
      value={tunnel.selectedProvider}
      onChange={(value) => tunnel.selectProvider(value as TunnelProviderId)}
      disabled={tunnel.isRunning || tunnel.isTransitioning}
    />
  )
}

function TunnelStatusRow({ tunnel }: { tunnel: TunnelCardState }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="flex items-center gap-2">
        <StatusBadge status={tunnel.status.status} label pulse={tunnel.status.pulse} />
        <span className="text-sm text-text-secondary">
          {tunnel.status.label}
          {tunnel.isRunning && tunnel.activeProvider ? ` via ${tunnel.activeProvider}` : ''}
        </span>
      </div>
      <ToggleField
        label={tunnel.isRunning ? 'Stop tunnel' : 'Start tunnel'}
        checked={tunnel.isRunning}
        onChange={(next) => void tunnel.handleToggle(next)}
        disabled={tunnel.isTransitioning || (!tunnel.isRunning && !tunnel.canStart)}
      />
    </div>
  )
}

function ProviderHintNote({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2 rounded-md bg-warning/10 p-card text-xs text-warning" role="note">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="flex flex-col gap-2">{children}</div>
    </div>
  )
}

function TokenCredentialSection({ tunnel, provider }: { tunnel: TunnelCardState; provider: TunnelProviderStatus }) {
  const [editing, setEditing] = useState(false)
  const [token, setToken] = useState('')
  const showInput = editing || !provider.credential_configured

  const save = async () => {
    if (!token.trim()) return
    const ok = await tunnel.saveCredential(token.trim())
    if (ok) {
      setToken('')
      setEditing(false)
    }
  }

  if (!showInput) {
    return (
      <div className="flex items-center justify-between gap-3 rounded-md border border-border bg-surface px-3 py-2">
        <span className="flex items-center gap-2 text-xs text-text-secondary">
          <KeyRound className="size-4" aria-hidden />
          Auth token saved
        </span>
        <span className="flex items-center gap-1">
          <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(true)}>
            Replace
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => void tunnel.clearCredential()}>
            Remove
          </Button>
        </span>
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-2">
      {!provider.credential_configured && (
        <p className="text-xs text-text-secondary">
          {provider.display_name} needs a (free) account auth token. Get one at{' '}
          <a
            href="https://dashboard.ngrok.com/get-started/your-authtoken"
            target="_blank"
            rel="noreferrer"
            className="underline"
          >
            dashboard.ngrok.com
          </a>{' '}
          and paste it below; it is stored encrypted on the backend.
        </p>
      )}
      <div className="flex items-end gap-2">
        <div className="flex-1">
          <InputField
            label="Auth token"
            type="password"
            value={token}
            onValueChange={setToken}
            placeholder="Paste your auth token"
            autoComplete="off"
          />
        </div>
        <Button
          type="button"
          size="sm"
          onClick={() => void save()}
          disabled={!token.trim() || tunnel.savingCredential}
        >
          {tunnel.savingCredential ? 'Saving...' : 'Save token'}
        </Button>
        {editing && (
          <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>
            Cancel
          </Button>
        )}
      </div>
    </div>
  )
}

function DeviceLoginSection({ tunnel, provider }: { tunnel: TunnelCardState; provider: TunnelProviderStatus }) {
  const prompt = tunnel.deviceLogin
  const pending = tunnel.connectingDevice === provider.provider_id
  if (provider.credential_configured) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-border bg-surface px-3 py-2 text-xs text-text-secondary">
        <KeyRound className="size-4" aria-hidden />
        Signed in with GitHub
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-text-secondary">
          Dev Tunnels signs in with a GitHub device code; the login is kept by the devtunnel CLI.
        </p>
        <Button type="button" size="sm" onClick={tunnel.connectDevice} disabled={pending}>
          {pending ? 'Waiting...' : 'Connect'}
        </Button>
      </div>
      {prompt && !prompt.already_logged_in && prompt.verification_uri && (
        <div className="flex flex-col gap-1.5 rounded-md border border-border bg-surface p-card text-xs" role="status">
          <span className="text-text-secondary">
            Open the link and enter this code. This card updates automatically once you authorise:
          </span>
          <span className="flex items-center gap-2">
            <code className="rounded bg-surface-raised px-2 py-1 font-mono text-sm text-foreground">
              {prompt.user_code}
            </code>
            <a
              href={prompt.verification_uri}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 underline"
            >
              {prompt.verification_uri}
              <ExternalLink className="size-3" aria-hidden />
            </a>
          </span>
        </div>
      )}
    </div>
  )
}

function TunnelCredentialSection({ tunnel }: { tunnel: TunnelCardState }) {
  const provider = tunnel.selectedStatus
  if (!provider || tunnel.isRunning) return null
  if (!provider.available) {
    return (
      <ProviderHintNote>
        <p>{provider.detail ?? `${provider.display_name} is not available on this host.`}</p>
      </ProviderHintNote>
    )
  }
  if (provider.credential_kind === 'token') {
    return <TokenCredentialSection tunnel={tunnel} provider={provider} />
  }
  if (provider.credential_kind === 'device_login') {
    return <DeviceLoginSection tunnel={tunnel} provider={provider} />
  }
  return (
    <p className="text-xs text-text-muted">
      No account needed: an ephemeral public URL is created when the tunnel starts.
      {provider.detail ? ` ${provider.detail}` : ''}
    </p>
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
            Opens a tunnel from a public URL to your local backend. Providers: Cloudflare quick tunnel
            (default, no account, random <code className="font-mono">*.trycloudflare.com</code> URL),
            ngrok (auth token required), and Dev Tunnels (devtunnel CLI, GitHub sign-in).
            Anyone with the URL can reach your backend (your auth + CSRF still apply).
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
              Production ingress. Every provider here exposes an ephemeral URL with no extra protection
              beyond SynthOrg&apos;s own auth.
            </li>
            <li>
              Sharing access with anyone outside your team. The URL is world-reachable; revoke it by
              stopping the tunnel.
            </li>
            <li>
              Anything you would not want the tunnel provider&apos;s edge servers to see in plaintext
              (TLS terminates at their edge and is re-encrypted onward).
            </li>
          </ul>
        </section>
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
        <TunnelProviderPicker tunnel={tunnel} />
        <TunnelStatusRow tunnel={tunnel} />
        <TunnelCredentialSection tunnel={tunnel} />
        <TunnelPublicUrl isRunning={tunnel.isRunning} publicUrl={tunnel.publicUrl} onCopy={() => void tunnel.copyUrl()} />
        <TunnelRunningNotices tunnel={tunnel} />
      </div>
      <TunnelIntroDialog tunnel={tunnel} />
    </SectionCard>
  )
}
