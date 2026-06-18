import { memo } from 'react'
import { Server } from 'lucide-react'
import { ProviderHealthBadge } from '@/components/ui/provider-health-badge'
import { cn } from '@/lib/utils'
import type { ProviderHealthSummary } from '@/api/types/providers'
import type { ProviderWithName } from '@/utils/providers'
import { formatTokenCount, formatCost } from '@/utils/providers'

interface ProviderCardProps {
  provider: ProviderWithName
  health: ProviderHealthSummary | null
  className?: string
}

function ProviderCardInner({ provider, health, className }: ProviderCardProps) {
  const displayName = provider.name
  const subtitle = provider.litellm_provider ?? provider.driver

  return (
    <div
      className={cn(
        'rounded-lg border border-border bg-card p-card',
        'transition-all duration-[var(--so-transition-default)]',
        'hover:bg-card-hover hover:-translate-y-px hover:shadow-[var(--so-shadow-card-hover)]',
        className,
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Server className="size-4 shrink-0 text-text-secondary" />
          <span className="truncate font-mono text-sm text-foreground">
            {displayName}
          </span>
        </div>
        {health && <ProviderHealthBadge status={health.health_status} label />}
      </div>

      {/* Body */}
      <div className="mt-3 flex flex-col gap-1.5">
        <div className="flex items-center gap-2 text-xs text-text-secondary">
          <span className="truncate">{subtitle}</span>
          <span className="text-text-muted">·</span>
          <span>{provider.auth_type.replaceAll('_', ' ')}</span>
        </div>

        {provider.base_url && (
          <span className="truncate font-mono text-xs text-text-muted">
            {provider.base_url}
          </span>
        )}

        <div className="mt-1 flex items-center gap-2">
          <span className="rounded-md bg-bg-surface px-1.5 py-0.5 text-xs font-mono text-text-secondary">
            {provider.models.length} model{provider.models.length !== 1 ? 's' : ''}
          </span>
          {health && (
            <span className="text-xs text-text-muted">
              {health.calls_last_24h} calls/24h
            </span>
          )}
          {health && (
            <span className="text-xs text-text-muted">
              {formatTokenCount(health.total_tokens_24h)} tok
            </span>
          )}
          {health && (
            <span className="text-xs text-text-muted">
              {formatCost(health.total_cost_24h)}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

export const ProviderCard = memo(ProviderCardInner)
