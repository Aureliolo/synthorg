import { ArrowLeft, Download, Pencil, RefreshCw, Trash2, Wifi } from 'lucide-react'
import { Link } from 'react-router'
import { ProviderHealthBadge } from '@/components/ui/provider-health-badge'
import { Button } from '@/components/ui/button'
import { ROUTES } from '@/router/routes'
import type { ProviderHealthSummary } from '@/api/types/providers'
import type { ProviderWithName } from '@/utils/providers'

interface ProviderDetailHeaderProps {
  provider: ProviderWithName
  health: ProviderHealthSummary | null
  onEdit: () => void
  onDelete: () => void
  onTestConnection: () => void
  testingConnection: boolean
  onRefresh?: () => void
  refreshing?: boolean
  onPullModel?: () => void
  supportsPull?: boolean
}

function ProviderTitleMeta({
  provider,
  health,
}: {
  provider: ProviderWithName
  health: ProviderHealthSummary | null
}) {
  const authLabel = provider.auth_type.replaceAll('_', ' ')
  return (
    <div className="flex flex-col gap-1.5 min-w-0">
      <div className="flex items-center gap-3">
        <h1 className="truncate text-xl font-semibold text-foreground">{provider.name}</h1>
        {health && <ProviderHealthBadge status={health.health_status} label />}
      </div>
      <div className="flex items-center gap-2 text-sm text-text-secondary">
        {provider.litellm_provider && (
          <>
            <span className="rounded bg-bg-surface px-1.5 py-0.5 font-mono text-xs">
              {provider.litellm_provider}
            </span>
            <span className="text-text-muted">|</span>
          </>
        )}
        <span>{authLabel}</span>
        {provider.base_url && (
          <>
            <span className="text-text-muted">|</span>
            <span className="truncate font-mono text-xs text-text-muted">
              {provider.base_url}
            </span>
          </>
        )}
      </div>
    </div>
  )
}

interface ProviderHeaderActionsProps {
  onEdit: () => void
  onDelete: () => void
  onTestConnection: () => void
  testingConnection: boolean
  onRefresh?: () => void
  refreshing: boolean
  onPullModel?: () => void
  supportsPull: boolean
}

function ProviderHeaderActions({
  onEdit,
  onDelete,
  onTestConnection,
  testingConnection,
  onRefresh,
  refreshing,
  onPullModel,
  supportsPull,
}: ProviderHeaderActionsProps) {
  return (
    <div className="flex items-center gap-2 shrink-0">
      {onRefresh && (
        <Button variant="outline" size="sm" onClick={onRefresh} disabled={refreshing}>
          <RefreshCw className={`size-3.5 mr-1.5 ${refreshing ? 'animate-spin' : ''}`} />
          {refreshing ? 'Refreshing...' : 'Refresh'}
        </Button>
      )}
      {supportsPull && onPullModel && (
        <Button variant="outline" size="sm" onClick={onPullModel}>
          <Download className="size-3.5 mr-1.5" />
          Pull Model
        </Button>
      )}
      <Button variant="outline" size="sm" onClick={onTestConnection} disabled={testingConnection}>
        <Wifi className="size-3.5 mr-1.5" />
        {testingConnection ? 'Testing...' : 'Test'}
      </Button>
      <Button variant="outline" size="sm" onClick={onEdit}>
        <Pencil className="size-3.5 mr-1.5" />
        Edit
      </Button>
      <Button variant="destructive" size="sm" onClick={onDelete}>
        <Trash2 className="size-3.5 mr-1.5" />
        Delete
      </Button>
    </div>
  )
}

export function ProviderDetailHeader({
  provider,
  health,
  onEdit,
  onDelete,
  onTestConnection,
  testingConnection,
  onRefresh,
  refreshing = false,
  onPullModel,
  supportsPull = false,
}: ProviderDetailHeaderProps) {
  return (
    <div className="flex flex-col gap-4">
      <Link
        to={ROUTES.PROVIDERS}
        className="inline-flex items-center gap-1.5 text-sm text-text-secondary hover:text-foreground transition-colors"
      >
        <ArrowLeft className="size-3.5" />
        Providers
      </Link>

      <div className="flex items-start justify-between gap-4">
        <ProviderTitleMeta provider={provider} health={health} />
        <ProviderHeaderActions
          onEdit={onEdit}
          onDelete={onDelete}
          onTestConnection={onTestConnection}
          testingConnection={testingConnection}
          onRefresh={onRefresh}
          refreshing={refreshing}
          onPullModel={onPullModel}
          supportsPull={supportsPull}
        />
      </div>
    </div>
  )
}
