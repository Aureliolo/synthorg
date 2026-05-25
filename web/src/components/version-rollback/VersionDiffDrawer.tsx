import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Drawer } from '@/components/ui/drawer'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import type {
  ReadOnlyVersionHistoryClient,
  VersionDiffResponse,
} from '@/api/endpoints/version-history'
import { VersionDiffPanel } from './VersionDiffPanel'

const log = createLogger('version-diff')

interface DiffResultState {
  key: string | null
  diff: VersionDiffResponse | null
  error: string | null
}

interface VersionDiffDrawerProps<T> {
  /**
   * Read-only client is sufficient: this component only calls
   * ``client.diff(...)``. Accepting the broader read-only contract
   * lets read-only domains (role, budget config, evaluation config,
   * company) render this drawer without forcing a rollback-capable
   * client at the call site.
   */
  client: ReadOnlyVersionHistoryClient<T>
  fromVersion: number | null
  toVersion: number | null
  open: boolean
  onClose: () => void
}

function useDiffRequest<T>(
  client: ReadOnlyVersionHistoryClient<T>,
  fromVersion: number | null,
  toVersion: number | null,
  open: boolean,
): { diff: VersionDiffResponse | null; error: string | null; isLoading: boolean } {
  const requestKey =
    open && fromVersion !== null && toVersion !== null
      ? `${fromVersion}-${toVersion}`
      : null
  const [result, setResult] = useState<DiffResultState>({
    key: null,
    diff: null,
    error: null,
  })
  const isLoading = requestKey !== null && result.key !== requestKey

  useEffect(() => {
    // Clear any prior result so the drawer never renders a stale diff
    // (or stale error) belonging to a previous version pair while the
    // current request is unresolved or invalid. ``requestKey === null``
    // covers the closed/null-version branch; the in-flight branch
    // resets only when the cached key no longer matches the pending
    // request, so re-renders with the same key don't flash empty.
    if (requestKey === null || fromVersion === null || toVersion === null) {
      setResult((prev) =>
        prev.key === null && prev.diff === null && prev.error === null
          ? prev
          : { key: null, diff: null, error: null },
      )
      return
    }
    setResult((prev) =>
      prev.key === requestKey ? prev : { key: null, diff: null, error: null },
    )
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
          setResult({ key: requestKey, diff: null, error: getErrorMessage(err) })
        }
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [client, fromVersion, toVersion, requestKey])

  return { diff: result.diff, error: result.error, isLoading }
}

/**
 * Shared (cross-domain) drawer that renders the field-level diff
 * between two version snapshots. ``client`` is the version-history
 * client for the resource scope (e.g. ``roleVersions(roleName)``);
 * ``fromVersion`` / ``toVersion`` identify the snapshots to compare.
 *
 * IMPORTANT: ``client`` MUST be a stable reference across renders
 * (e.g. constructed once via ``useMemo`` or imported as a
 * module-level singleton). This component depends on ``client`` in
 * its diff-fetch effect; passing a freshly-constructed client on
 * every render would trigger an infinite fetch loop.
 */
export function VersionDiffDrawer<T>({
  client,
  fromVersion,
  toVersion,
  open,
  onClose,
}: VersionDiffDrawerProps<T>) {
  const { diff, error, isLoading } = useDiffRequest(client, fromVersion, toVersion, open)
  const title = fromVersion !== null && toVersion !== null
    ? `Diff · v${fromVersion} -> v${toVersion}`
    : 'Diff'
  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={title}
      ariaLabel="Version diff"
      width="wide"
    >
      <div className="flex flex-col gap-section-gap p-card">
        <VersionDiffPanel diff={diff} error={error} isLoading={isLoading} />
        <div className="flex justify-end pt-card">
          <Button variant="secondary" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </Drawer>
  )
}
