import { Activity } from 'lucide-react'

import type { SignalsResponse } from '@/api/endpoints/meta'
import { EmptyState } from '@/components/ui/empty-state'
import { StatusBadge } from '@/components/ui/status-badge'

interface MetaSignalOverviewProps {
  signals: SignalsResponse | null
}

export function MetaSignalOverview({ signals }: MetaSignalOverviewProps) {
  if (!signals || signals.domains.length === 0) {
    return (
      <EmptyState
        icon={Activity}
        title="No signal data"
        description="Signal data will appear here when the meta-loop runs its first cycle."
      />
    )
  }

  return (
    <div className="grid grid-cols-2 gap-grid-gap sm:grid-cols-3 lg:grid-cols-4">
      {signals.domains.map((domain) => (
        <div
          key={domain.name}
          className="flex items-center gap-2 rounded-md border border-border p-card"
        >
          <StatusBadge
            status={domain.status === 'available' ? 'active' : 'idle'}
          />
          <span className="text-sm font-medium capitalize text-foreground">
            {domain.name}
          </span>
        </div>
      ))}
    </div>
  )
}
