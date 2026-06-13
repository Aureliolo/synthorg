import type { ConnectionHealthStatus } from '@/api/types/integrations'
import type { ProviderHealthStatus } from '@/api/types/providers'
import { ProviderHealthBadge } from './provider-health-badge'

const CONNECTION_TO_PROVIDER: Record<
  ConnectionHealthStatus,
  ProviderHealthStatus
> = {
  healthy: 'up',
  degraded: 'degraded',
  unhealthy: 'down',
  unknown: 'unknown',
}

export interface ConnectionHealthBadgeProps {
  status: ConnectionHealthStatus
  label?: boolean | undefined
  pulse?: boolean | undefined
  className?: string | undefined
}

/**
 * Connection health status indicator.
 *
 * Thin wrapper over `ProviderHealthBadge` that maps the backend's
 * connection health enum (healthy/degraded/unhealthy/unknown) onto
 * the shared provider health primitive (up/degraded/down/unknown).
 * This is the single source of truth for that mapping so call sites
 * don't have to reconcile the enum mismatch themselves.
 */
export function ConnectionHealthBadge({
  status,
  label = false,
  pulse = false,
  className,
}: ConnectionHealthBadgeProps) {
  return (
    <ProviderHealthBadge
      status={CONNECTION_TO_PROVIDER[status]}
      label={label}
      pulse={pulse}
      className={className}
    />
  )
}
