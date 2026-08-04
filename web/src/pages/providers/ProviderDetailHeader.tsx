import { ArrowLeft, Download, Pencil, RefreshCw, Trash2, Wifi } from 'lucide-react'
import { Link } from 'react-router'
import { ProviderHealthBadge } from '@/components/ui/provider-health-badge'
import { StatusPill } from '@/components/ui/status-pill'
import { Button } from '@/components/ui/button'
import { ROUTES } from '@/router/routes'
import type { ProviderHealthSummary } from '@/api/types/providers'
import type { ProviderWithName } from '@/utils/providers'

const EMPTY_TEST_MODEL_IDS: readonly string[] = []

interface ProviderDetailHeaderProps {
  provider: ProviderWithName
  health: ProviderHealthSummary | null
  onEdit: () => void
  onDelete: () => void
  /** Run the connection test, optionally against a specific model id. */
  onTestConnection: (model?: string) => void
  testingConnection: boolean
  /** Model ids selectable for the connection test (empty hides the picker). */
  testModelIds?: readonly string[]
  testModel?: string
  onTestModelChange?: (model: string) => void
  onRefresh?: () => void
  refreshing?: boolean
  onPullModel?: () => void
  supportsPull?: boolean
  /** Call the provider now so its status reflects the present. */
  onRecheckHealth: () => void
  recheckingHealth: boolean
}

function ProviderTitleMeta({
  provider,
  health,
  onRecheckHealth,
  recheckingHealth,
}: {
  provider: ProviderWithName
  health: ProviderHealthSummary | null
  onRecheckHealth: () => void
  recheckingHealth: boolean
}) {
  const authLabel = provider.auth_type.replaceAll('_', ' ')
  return (
    <div className="flex flex-col gap-1.5 min-w-0">
      <div className="flex items-center gap-3">
        <h1 className="truncate text-xl font-semibold text-foreground">{provider.name}</h1>
        {health && <ProviderHealthBadge status={health.health_status} label />}
        {/* Beside the badge rather than in the action row: it corrects that
            badge, and nothing else re-derives it between probe cycles. */}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-label="Recheck this provider's health"
          title="Call this provider now and update its status"
          disabled={recheckingHealth}
          onClick={onRecheckHealth}
        >
          <RefreshCw
            className={`size-3.5 mr-1.5 ${recheckingHealth ? 'animate-spin' : ''}`}
          />
          {recheckingHealth ? 'Checking...' : 'Recheck'}
        </Button>
        {!provider.agent_eligible && (
          <StatusPill
            tone="warning"
            ariaLabel="This provider is excluded from agent model assignment"
          >
            Agents off
          </StatusPill>
        )}
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
  onTestConnection: (model?: string) => void
  testingConnection: boolean
  testModelIds: readonly string[]
  testModel: string
  onTestModelChange: (model: string) => void
  onRefresh?: (() => void) | undefined
  refreshing: boolean
  onPullModel?: (() => void) | undefined
  supportsPull: boolean
}

function ProviderHeaderActions({
  onEdit,
  onDelete,
  onTestConnection,
  testingConnection,
  testModelIds,
  testModel,
  onTestModelChange,
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
      {testModelIds.length > 0 && (
        <select
          value={testModel}
          onChange={(e) => onTestModelChange(e.target.value)}
          aria-label="Model to test"
          title="Model used for the connection test"
          className="h-8 max-w-[12rem] rounded-md border border-border bg-card px-2 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <option value="">Auto (smallest)</option>
          {testModelIds.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      )}
      <Button
        variant="outline"
        size="sm"
        onClick={() => onTestConnection(testModel || undefined)}
        disabled={testingConnection}
      >
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
  testModelIds = EMPTY_TEST_MODEL_IDS,
  testModel = '',
  onTestModelChange,
  onRefresh,
  refreshing = false,
  onPullModel,
  supportsPull = false,
  onRecheckHealth,
  recheckingHealth,
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
        <ProviderTitleMeta
          provider={provider}
          health={health}
          onRecheckHealth={onRecheckHealth}
          recheckingHealth={recheckingHealth}
        />
        <ProviderHeaderActions
          onEdit={onEdit}
          onDelete={onDelete}
          onTestConnection={onTestConnection}
          testingConnection={testingConnection}
          testModelIds={testModelIds}
          testModel={testModel}
          onTestModelChange={onTestModelChange ?? (() => undefined)}
          onRefresh={onRefresh}
          refreshing={refreshing}
          onPullModel={onPullModel}
          supportsPull={supportsPull}
        />
      </div>
    </div>
  )
}
