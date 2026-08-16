import { useEffect, useState } from 'react'
import { getProviderConfigDiagnostics } from '@/api/endpoints/providers'
import { ErrorBanner } from '@/components/ui/error-banner'
import { createLogger } from '@/lib/logger'
import type { ProviderConfigDiagnostics } from '@/api/types/providers'

const log = createLogger('provider-config-diagnostics')

export interface ProviderConfigDiagnosticsBannerProps {
  className?: string | undefined
}

/**
 * Tell an operator when their provider config could not be read.
 *
 * An empty provider list means two opposite things: nothing has been
 * configured yet, or a configuration exists that this build cannot read.
 * The backend also logs and notifies, but a log has scrolled by the time
 * anyone looks and a notification needs a sink configured, so this is the
 * surface that is still there on the next page load.
 *
 * Silent when the config reads cleanly, which is almost always.
 */
export function ProviderConfigDiagnosticsBanner({
  className,
}: ProviderConfigDiagnosticsBannerProps) {
  const [diagnostics, setDiagnostics] = useState<ProviderConfigDiagnostics | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const result = await getProviderConfigDiagnostics()
        if (!cancelled) setDiagnostics(result)
      } catch (err) {
        // Diagnostics are what explains another failure, never the
        // subject of one: a banner about the banner would be noise on a
        // page that is already telling the operator something is wrong.
        log.warn('Provider config diagnostics unavailable', err)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  if (diagnostics === null || diagnostics.status === 'ok') return null

  const rejected = diagnostics.rejected.map((entry) => entry.name)
  const unreadable = diagnostics.status === 'unreadable'

  return (
    <ErrorBanner
      className={className}
      severity={unreadable ? 'error' : 'warning'}
      title={
        unreadable
          ? 'Provider configuration could not be read'
          : 'Some provider connections could not be read'
      }
      description={
        <ProviderConfigDiagnosticsDetail
          rejected={rejected}
          detail={diagnostics.detail}
          unreadable={unreadable}
        />
      }
    />
  )
}

function ProviderConfigDiagnosticsDetail({
  rejected,
  detail,
  unreadable,
}: {
  rejected: readonly string[]
  detail: string | null
  unreadable: boolean
}) {
  return (
    <>
      <p>
        {rejected.length > 0
          ? `Connections that could not be read: ${rejected.join(', ')}.`
          : (detail ?? 'The stored configuration is not in a readable shape.')}
      </p>
      <p className="mt-1">
        {unreadable
          ? 'This deployment is serving with no providers until the configuration is corrected. It is not an unconfigured company.'
          : 'The remaining connections are serving normally. Re-save each listed connection to correct it.'}
      </p>
    </>
  )
}
