import { memo } from 'react'
import { Link } from 'react-router'
import { ProviderCard } from './ProviderCard'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { EmptyState } from '@/components/ui/empty-state'
import { ROUTES } from '@/router/routes'
import { Server } from 'lucide-react'
import type { ProviderHealthSummary } from '@/api/types/providers'
import type { ProviderWithName } from '@/utils/providers'

interface ProviderGridItemProps {
  provider: ProviderWithName
  health: ProviderHealthSummary | null
  selected?: boolean
  onToggleSelect?: (name: string) => void
}

const ProviderGridItem = memo(function ProviderGridItem({
  provider,
  health,
  selected,
  onToggleSelect,
}: ProviderGridItemProps) {
  return (
    <StaggerItem>
      <div className="relative">
        {onToggleSelect && (
          <label
            className="absolute left-2 top-2 z-10 flex h-6 w-6 cursor-pointer items-center justify-center rounded border border-border bg-card shadow-sm"
            onClick={(e) => e.stopPropagation()}
          >
            <input
              type="checkbox"
              checked={selected ?? false}
              onChange={() => onToggleSelect(provider.name)}
              aria-label={`Select provider ${provider.name}`}
              className="h-4 w-4 cursor-pointer accent-accent"
            />
          </label>
        )}
        <Link
          to={ROUTES.PROVIDER_DETAIL.replace(
            ':providerName',
            encodeURIComponent(provider.name),
          )}
          className="block"
        >
          <ProviderCard provider={provider} health={health} />
        </Link>
      </div>
    </StaggerItem>
  )
})

interface ProviderGridViewProps {
  providers: readonly ProviderWithName[]
  healthMap: Record<string, ProviderHealthSummary>
  onAddProvider?: () => void
  selectedIds?: ReadonlySet<string>
  onToggleSelect?: (name: string) => void
}

export function ProviderGridView({
  providers,
  healthMap,
  onAddProvider,
  selectedIds,
  onToggleSelect,
}: ProviderGridViewProps) {
  if (providers.length === 0) {
    return (
      <EmptyState
        icon={Server}
        title="No providers configured"
        description="Add an LLM provider to get started with your synthetic organization."
        action={onAddProvider ? { label: 'Add Provider', onClick: onAddProvider } : undefined}
      />
    )
  }

  return (
    <StaggerGroup className="grid grid-cols-3 gap-grid-gap max-[1023px]:grid-cols-2 max-[767px]:grid-cols-1">
      {providers.map((provider) => (
        <ProviderGridItem
          key={provider.name}
          provider={provider}
          health={healthMap[provider.name] ?? null}
          selected={selectedIds?.has(provider.name)}
          onToggleSelect={onToggleSelect}
        />
      ))}
    </StaggerGroup>
  )
}
