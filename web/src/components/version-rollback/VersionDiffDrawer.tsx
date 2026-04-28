import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Drawer } from '@/components/ui/drawer'
import { ErrorBanner } from '@/components/ui/error-banner'
import { Skeleton } from '@/components/ui/skeleton'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import type { VersionDiffResponse, VersionHistoryClient } from '@/api/endpoints/version-history'

const log = createLogger('version-diff')

interface VersionDiffDrawerProps<T> {
  client: VersionHistoryClient<T>
  fromVersion: number | null
  toVersion: number | null
  open: boolean
  onClose: () => void
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '∅'
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

/**
 * Shared (cross-domain) drawer that renders the field-level diff
 * between two version snapshots.  ``client`` is the version-history
 * client for the resource scope (e.g. ``roleVersions(roleName)``);
 * ``fromVersion`` / ``toVersion`` identify the snapshots to compare.
 */
export function VersionDiffDrawer<T>({
  client,
  fromVersion,
  toVersion,
  open,
  onClose,
}: VersionDiffDrawerProps<T>) {
  // Diff state is keyed by the (fromVersion, toVersion) pair.  When
  // the pair changes (or the drawer reopens), the inner result key
  // shifts and the previous diff is treated as stale.  Avoids
  // synchronous setState calls inside the effect body, which the
  // ``eslint-react/set-state-in-effect`` rule prohibits.
  const requestKey =
    open && fromVersion !== null && toVersion !== null
      ? `${fromVersion}-${toVersion}`
      : null
  const [result, setResult] = useState<{
    key: string | null
    diff: VersionDiffResponse | null
    error: string | null
  }>({ key: null, diff: null, error: null })
  const { key: resultKey, diff, error } = result
  const isLoading = requestKey !== null && resultKey !== requestKey

  useEffect(() => {
    if (requestKey === null || fromVersion === null || toVersion === null) return
    let cancelled = false
    const run = async (): Promise<void> => {
      try {
        const response = await client.diff(fromVersion, toVersion)
        if (!cancelled) {
          setResult({ key: requestKey, diff: response, error: null })
        }
      } catch (err) {
        log.warn('Failed to load version diff:', getErrorMessage(err))
        if (!cancelled) {
          setResult({
            key: requestKey,
            diff: null,
            error: getErrorMessage(err),
          })
        }
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [client, fromVersion, toVersion, requestKey])

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={
        fromVersion !== null && toVersion !== null
          ? `Diff · v${fromVersion} -> v${toVersion}`
          : 'Diff'
      }
      ariaLabel="Version diff"
      width="wide"
    >
      <div className="flex flex-col gap-section-gap p-card">
        {error && (
          <ErrorBanner
            severity="error"
            title="Could not load diff"
            description={error}
          />
        )}

        {isLoading && (
          <div className="flex flex-col gap-grid-gap">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-16 w-full" />
            ))}
          </div>
        )}

        {!isLoading && diff !== null && diff.entries.length === 0 && (
          <p className="text-sm text-text-secondary">
            No field-level changes between these versions.
          </p>
        )}

        {!isLoading && diff !== null && diff.entries.length > 0 && (
          <ul className="flex flex-col gap-grid-gap">
            {diff.entries.map((entry) => (
              <li
                key={entry.path}
                className="rounded-md border border-border bg-card p-card"
              >
                <p className="mb-1 font-mono text-xs text-text-secondary">
                  {entry.path}
                </p>
                <div className="grid grid-cols-1 gap-grid-gap sm:grid-cols-2">
                  <div>
                    <p className="text-xs text-text-secondary">Before</p>
                    <pre className="overflow-x-auto rounded-md bg-surface p-2 font-mono text-xs text-foreground">
                      {formatValue(entry.before)}
                    </pre>
                  </div>
                  <div>
                    <p className="text-xs text-text-secondary">After</p>
                    <pre className="overflow-x-auto rounded-md bg-surface p-2 font-mono text-xs text-foreground">
                      {formatValue(entry.after)}
                    </pre>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}

        <div className="flex justify-end pt-card">
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </Drawer>
  )
}
