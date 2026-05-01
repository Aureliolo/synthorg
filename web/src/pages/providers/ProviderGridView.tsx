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
}

const ProviderGridItem = memo(function ProviderGridItem({
  provider,
  health,
}: ProviderGridItemProps) {
  return (
    <StaggerItem>
      <Link
        to={ROUTES.PROVIDER_DETAIL.replace(
          ':providerName',
          encodeURIComponent(provider.name),
        )}
        className="block"
      >
        <ProviderCard provider={provider} health={health} />
      </Link>
    </StaggerItem>
  )
})

interface ProviderGridViewProps {
  providers: readonly ProviderWithName[]
  healthMap: Record<string, ProviderHealthSummary>
  onAddProvider?: () => void
}

export function ProviderGridView({
  providers,
  healthMap,
  onAddProvider,
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
        />
      ))}
    </StaggerGroup>
  )
}
