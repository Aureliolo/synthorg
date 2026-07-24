import { useCallback, useRef, useState } from 'react'
import { scanAccessibleRepos } from '@/api/endpoints/connections'
import { type ForgeAccessibleRepo } from '@/api/types/integrations'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { createLogger } from '@/lib/logger'
import { cn } from '@/lib/utils'
import { getCrudErrorTitle } from '@/utils/errors'

const log = createLogger('RepoScopePicker')

/** Wire key a repository is scoped by: ``owner/repo``. */
function repoKey(repo: ForgeAccessibleRepo): string {
  return `${repo.owner}/${repo.repo}`
}

/**
 * Whether a scope entry is an ``owner/*`` wildcard. The backend validator
 * permits these, so the picker must treat them as broad grants rather than
 * exact repository keys: a concrete repo under the wildcard is in scope even
 * though its own key is not selected, so a per-repo checkbox cannot narrow it.
 */
function isWildcardScope(key: string): boolean {
  return key.endsWith('/*')
}

type ScanState =
  | { readonly status: 'idle' }
  | { readonly status: 'loading' }
  | { readonly status: 'error'; readonly message: string }
  | { readonly status: 'loaded'; readonly repos: readonly ForgeAccessibleRepo[] }

export interface RepoScopePickerProps {
  /** The connection whose token is scanned for reachable repositories. */
  connectionName: string
  /** Currently in-scope repository keys (``owner/repo``). */
  selected: readonly string[]
  /** Fired with the next selection when the operator ticks/unticks a repo. */
  onChange: (repos: readonly string[]) => void
}

function PermissionBadge({ permission }: { permission: ForgeAccessibleRepo['permission'] }) {
  return (
    <span
      className={cn(
        'rounded-full bg-surface px-2 py-0.5 text-micro font-medium uppercase tracking-wide',
        permission === 'admin' && 'text-accent',
        permission === 'write' && 'text-text-secondary',
        permission === 'read' && 'text-text-muted',
      )}
    >
      {permission}
    </span>
  )
}

function RepoRow({
  repo,
  checked,
  onToggle,
}: {
  repo: ForgeAccessibleRepo
  checked: boolean
  onToggle: (checked: boolean) => void
}) {
  const key = repoKey(repo)
  const labelId = `repo-scope-${key}`
  return (
    <label
      htmlFor={labelId}
      className={cn(
        'flex cursor-pointer items-center gap-2 rounded-md border border-border bg-card p-3',
        'transition-colors hover:bg-card-hover',
      )}
    >
      <Checkbox id={labelId} checked={checked} onCheckedChange={onToggle} aria-label={key} />
      <span className="flex-1 text-sm text-foreground">{key}</span>
      {repo.private && (
        <span className="text-micro uppercase tracking-wide text-text-muted">private</span>
      )}
      <PermissionBadge permission={repo.permission} />
    </label>
  )
}

/**
 * A repository that is in scope but absent from the latest scan. Rendered so
 * the scope stays revocable: without a row it could not be unticked here, and
 * a scope entry left behind reactivates the moment token access returns.
 */
function UnscannedRepoRow({
  scopeKey,
  unreachable,
  onRemove,
}: {
  scopeKey: string
  unreachable: boolean
  onRemove: () => void
}) {
  const labelId = `repo-scope-unscanned-${scopeKey}`
  return (
    <label
      htmlFor={labelId}
      className={cn(
        'flex cursor-pointer items-center gap-2 rounded-md border border-border bg-card p-3',
        'transition-colors hover:bg-card-hover',
      )}
    >
      <Checkbox
        id={labelId}
        checked
        onCheckedChange={onRemove}
        aria-label={`Remove ${scopeKey} from scope`}
      />
      <span className="flex-1 text-sm text-foreground">{scopeKey}</span>
      {unreachable && (
        <span className="text-micro uppercase tracking-wide text-warning">not reachable</span>
      )}
    </label>
  )
}

/**
 * A broad ``owner/*`` grant. The concrete repositories it covers stay
 * unchecked in the scan list because their exact keys are not selected, so the
 * only way to narrow it is to revoke the wildcard itself: this row makes that
 * explicit and removable rather than leaving a silent org-wide grant in place.
 */
function WildcardScopeRow({ scopeKey, onRemove }: { scopeKey: string; onRemove: () => void }) {
  const labelId = `repo-scope-wildcard-${scopeKey}`
  return (
    <label
      htmlFor={labelId}
      className={cn(
        'flex cursor-pointer items-center gap-2 rounded-md border border-warning/40 bg-card p-3',
        'transition-colors hover:bg-card-hover',
      )}
    >
      <Checkbox
        id={labelId}
        checked
        onCheckedChange={onRemove}
        aria-label={`Remove ${scopeKey} from scope`}
      />
      <span className="flex-1 text-sm text-foreground">{scopeKey}</span>
      <span className="text-micro uppercase tracking-wide text-warning">
        every repository under this owner
      </span>
    </label>
  )
}

function ScanResults({
  scan,
  selected,
  onToggle,
}: {
  scan: ScanState
  selected: readonly string[]
  onToggle: (key: string, next: boolean) => void
}) {
  if (scan.status === 'error') return <p className="text-xs text-danger">{scan.message}</p>
  if (scan.status !== 'loaded') return null
  if (scan.repos.length === 0) {
    return <p className="text-xs text-text-muted">This token cannot reach any repositories.</p>
  }
  const selectedSet = new Set(selected)
  return (
    <div className="flex flex-col gap-2">
      {scan.repos.map((repo) => {
        const key = repoKey(repo)
        return (
          <RepoRow
            key={key}
            repo={repo}
            checked={selectedSet.has(key)}
            onToggle={(next) => onToggle(key, next)}
          />
        )
      })}
    </div>
  )
}

function WildcardScope({
  selected,
  onRemove,
}: {
  selected: readonly string[]
  onRemove: (key: string) => void
}) {
  const wildcards = selected.filter(isWildcardScope)
  if (wildcards.length === 0) return null
  return (
    <div className="flex flex-col gap-2">
      {wildcards.map((key) => (
        <WildcardScopeRow
          key={key}
          scopeKey={key}
          onRemove={() => {
            onRemove(key)
          }}
        />
      ))}
    </div>
  )
}

function UnscannedScope({
  scan,
  selected,
  onRemove,
}: {
  scan: ScanState
  selected: readonly string[]
  onRemove: (key: string) => void
}) {
  const scanned =
    scan.status === 'loaded' ? new Set(scan.repos.map(repoKey)) : new Set<string>()
  // Wildcards are rendered by WildcardScope, not as unscanned concrete repos.
  const unscanned = selected.filter((key) => !isWildcardScope(key) && !scanned.has(key))
  if (unscanned.length === 0) return null
  return (
    <div className="flex flex-col gap-2">
      {unscanned.map((key) => (
        <UnscannedRepoRow
          key={key}
          scopeKey={key}
          unreachable={scan.status === 'loaded'}
          onRemove={() => {
            onRemove(key)
          }}
        />
      ))}
    </div>
  )
}

/**
 * Discovery-assisted least-privilege repository scope. Scans the forge
 * connection's token for reachable repositories and lets the operator tick the
 * ones the agent may act on; an empty selection denies every repository
 * (fail-closed). The scan is a live read against the forge (never persisted),
 * so it is deferred behind an explicit button rather than run on mount.
 *
 * Every in-scope repository is rendered whether or not the scan returned it,
 * so the scope is always revocable: one the token can no longer see would
 * otherwise be stuck in scope and would reactivate if access came back.
 */
export function RepoScopePicker({ connectionName, selected, onChange }: RepoScopePickerProps) {
  const [scan, setScan] = useState<ScanState>({ status: 'idle' })

  // The picker stays mounted while the form switches connections, so a scan
  // must belong to the connection currently shown. Reset the scan the moment
  // the prop changes (adjust-state-during-render, not an effect), and drop any
  // in-flight response whose connection is no longer active. Without this a
  // slow scan of the previous connection overwrites this one's repo list.
  const activeConnectionRef = useRef(connectionName)
  if (activeConnectionRef.current !== connectionName) {
    activeConnectionRef.current = connectionName
    setScan({ status: 'idle' })
  }

  const runScan = useCallback(async () => {
    const requested = connectionName
    setScan({ status: 'loading' })
    try {
      const repos = await scanAccessibleRepos(connectionName)
      if (activeConnectionRef.current !== requested) return
      setScan({ status: 'loaded', repos })
    } catch (err) {
      if (activeConnectionRef.current !== requested) return
      log.error('accessible-repo scan failed', err)
      setScan({ status: 'error', message: getCrudErrorTitle(err, 'Scan failed').title })
    }
  }, [connectionName])

  const toggle = useCallback(
    (key: string, next: boolean) => {
      const remaining = selected.filter((r) => r !== key)
      onChange(next ? [...remaining, key] : remaining)
    },
    [selected, onChange],
  )

  const remove = useCallback(
    (key: string) => {
      toggle(key, false)
    },
    [toggle],
  )

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-foreground">Repository scope</span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={scan.status === 'loading'}
          onClick={() => void runScan()}
        >
          {scan.status === 'loading' ? 'Scanning...' : 'Scan repositories'}
        </Button>
      </div>
      <p className="text-xs text-text-secondary">
        Agents may only act on the repositories ticked below. An empty selection denies every
        repository.
      </p>

      <WildcardScope selected={selected} onRemove={remove} />
      <ScanResults scan={scan} selected={selected} onToggle={toggle} />
      <UnscannedScope scan={scan} selected={selected} onRemove={remove} />

      {selected.length > 0 && (
        <p className="text-xs text-text-secondary">
          {selected.length} {selected.length === 1 ? 'repository' : 'repositories'} in scope:{' '}
          {selected.join(', ')}
        </p>
      )}
    </div>
  )
}
