import { useCallback, useState } from 'react'
import { useParams, useNavigate } from 'react-router'
import { useProviderDetailData } from '@/hooks/useProviderDetailData'
import { useProvidersData } from '@/hooks/useProvidersData'
import { useProvidersStore } from '@/stores/providers'
import { DetailNavBar } from '@/components/ui/detail-nav-bar'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ErrorBanner } from '@/components/ui/error-banner'
import { EmptyState } from '@/components/ui/empty-state'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ROUTES } from '@/router/routes'
import {
  useDetailNavigation,
  useDetailNavigationCallbacks,
} from '@/hooks/use-detail-navigation'
import { ProviderDetailHeader } from './providers/ProviderDetailHeader'
import { ProviderHealthMetrics } from './providers/ProviderHealthMetrics'
import { ProviderModelList } from './providers/ProviderModelList'
import { ProviderDetailSkeleton } from './providers/ProviderDetailSkeleton'
import { ProviderFormModal } from './providers/ProviderFormModal'
import { TestConnectionResult } from './providers/TestConnectionResult'
import { ModelPullDialog } from './providers/ModelPullDialog'
import { ModelConfigDrawer } from './providers/ModelConfigDrawer'
import { AuditLogDrawer } from './providers/AuditLogDrawer'
import { RateLimitsDrawer } from './providers/RateLimitsDrawer'
import { CredentialsRotateDialog } from './providers/CredentialsRotateDialog'
import { AddManualModelDialog } from './providers/AddManualModelDialog'
import { SyncModelsConfirmDialog } from './providers/SyncModelsConfirmDialog'
import { Button } from '@/components/ui/button'
import { Server } from 'lucide-react'
import type { ProviderModelResponse } from '@/api/types/providers'

export default function ProviderDetailPage() {
  const { providerName } = useParams<{ providerName: string }>()
  const navigate = useNavigate()
  const decodedName = providerName ?? ''

  const {
    provider,
    models,
    health,
    loading,
    error,
    testConnectionResult,
    testingConnection,
  } = useProviderDetailData(decodedName)

  const [editOpen, setEditOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [pullOpen, setPullOpen] = useState(false)
  const [configModel, setConfigModel] = useState<ProviderModelResponse | null>(null)
  const [deleteModelId, setDeleteModelId] = useState<string | null>(null)
  const [auditOpen, setAuditOpen] = useState(false)
  const [rateLimitsOpen, setRateLimitsOpen] = useState(false)
  const [rotateOpen, setRotateOpen] = useState(false)
  const [addModelOpen, setAddModelOpen] = useState(false)
  const [syncOpen, setSyncOpen] = useState(false)

  const discoveringModels = useProvidersStore((s) => s.discoveringModels)
  const deletingModel = useProvidersStore((s) => s.deletingModel)

  // Walk the parent provider list (filtered + sorted) so prev/next on
  // this detail page steps through the same providers the operator
  // saw on ProvidersPage. ``ProviderWithName.name`` is the URL key.
  const { filteredProviders } = useProvidersData()
  const routeForProvider = useCallback(
    (item: { id: string }) =>
      ROUTES.PROVIDER_DETAIL.replace(':providerName', encodeURIComponent(item.id)),
    [],
  )
  const navItems = filteredProviders.map((p) => ({ id: p.name }))
  const nav = useDetailNavigation({
    items: navItems,
    currentId: decodedName,
    routeFor: routeForProvider,
  })
  const { goPrev, goNext } = useDetailNavigationCallbacks(nav)

  // Loading state
  if (loading && !provider) {
    return <ProviderDetailSkeleton />
  }

  // Error state
  if (error && !provider) {
    return (
      <div className="flex flex-col gap-section-gap">
        <ErrorBanner
          severity="error"
          title="Could not load provider"
          description={error}
        />
      </div>
    )
  }

  if (!provider) {
    return (
      <EmptyState
        icon={Server}
        title="Provider not found"
        description="The provider you are looking for does not exist or has been removed."
        action={{ label: 'Back to Providers', onClick: () => navigate(ROUTES.PROVIDERS) }}
      />
    )
  }

  return (
    <div className="flex flex-col gap-section-gap">
      <DetailNavBar
        canPrev={nav.canPrev}
        canNext={nav.canNext}
        onPrev={goPrev}
        onNext={goNext}
        position={nav.position}
      />
      {/* Partial error banner */}
      {error && (
        <ErrorBanner
          severity="warning"
          title="Some provider data could not be refreshed"
          description={error}
        />
      )}

      {/* Header */}
      <ErrorBoundary level="section">
        <ProviderDetailHeader
          provider={provider}
          health={health}
          onEdit={() => setEditOpen(true)}
          onDelete={() => setDeleteOpen(true)}
          onTestConnection={() => {
            useProvidersStore.getState().testConnection(decodedName)
          }}
          testingConnection={testingConnection}
          onRefresh={() => {
            useProvidersStore.getState().discoverModels(
              decodedName,
              provider.preset_name ?? undefined,
            )
          }}
          refreshing={discoveringModels}
          onPullModel={() => setPullOpen(true)}
          supportsPull={provider.supports_model_pull}
        />
      </ErrorBoundary>

      {/* Capability action bar */}
      <div className="flex flex-wrap gap-grid-gap">
        <Button variant="secondary" onClick={() => setAuditOpen(true)}>
          Audit log
        </Button>
        <Button variant="secondary" onClick={() => setRateLimitsOpen(true)}>
          Rate limits
        </Button>
        <Button variant="secondary" onClick={() => setRotateOpen(true)}>
          Rotate credentials
        </Button>
        <Button variant="secondary" onClick={() => setAddModelOpen(true)}>
          Add model manually
        </Button>
        <Button variant="secondary" onClick={() => setSyncOpen(true)}>
          Sync models
        </Button>
      </div>

      {/* Test connection result */}
      {testConnectionResult && (
        <TestConnectionResult result={testConnectionResult} />
      )}

      {/* Health metrics */}
      {health && (
        <ErrorBoundary level="section">
          <ProviderHealthMetrics health={health} />
        </ErrorBoundary>
      )}

      {/* Model list */}
      <ErrorBoundary level="section">
        <ProviderModelList
          models={models}
          supportsDelete={provider.supports_model_delete}
          supportsConfig={provider.supports_model_config}
          onDelete={(modelId) => setDeleteModelId(modelId)}
          onConfigure={(model) => setConfigModel(model)}
        />
      </ErrorBoundary>

      {/* Edit drawer */}
      <ProviderFormModal
        open={editOpen}
        onClose={() => setEditOpen(false)}
        mode="edit"
        provider={provider}
      />

      {/* Delete provider confirmation */}
      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Delete Provider"
        description={`Are you sure you want to delete "${provider.name}"? This action cannot be undone.`}
        variant="destructive"
        confirmLabel="Delete"
        onConfirm={async () => {
          const success = await useProvidersStore.getState().deleteProvider(decodedName)
          if (success) {
            navigate(ROUTES.PROVIDERS)
          }
          setDeleteOpen(false)
        }}
      />

      {/* Pull model dialog */}
      <ModelPullDialog
        providerName={decodedName}
        open={pullOpen}
        onClose={() => setPullOpen(false)}
      />

      {/* Model config drawer */}
      <ModelConfigDrawer
        providerName={decodedName}
        model={configModel}
        open={configModel !== null}
        onClose={() => setConfigModel(null)}
      />

      {/* Delete model confirmation */}
      <ConfirmDialog
        open={deleteModelId !== null}
        onOpenChange={(open) => { if (!open) setDeleteModelId(null) }}
        title="Delete Model"
        description={`Are you sure you want to delete "${deleteModelId ?? ''}" from this provider? This will remove the model from the local instance.`}
        variant="destructive"
        confirmLabel="Delete"
        loading={deletingModel}
        onConfirm={async () => {
          if (deleteModelId) {
            await useProvidersStore.getState().deleteModel(decodedName, deleteModelId)
          }
          setDeleteModelId(null)
        }}
      />

      {/* Audit log drawer */}
      <AuditLogDrawer
        providerName={decodedName}
        open={auditOpen}
        onClose={() => setAuditOpen(false)}
      />

      {/* Rate-limits drawer */}
      <RateLimitsDrawer
        providerName={decodedName}
        open={rateLimitsOpen}
        onClose={() => setRateLimitsOpen(false)}
      />

      {/* Credentials rotate dialog */}
      <CredentialsRotateDialog
        providerName={decodedName}
        provider={provider}
        open={rotateOpen}
        onClose={() => setRotateOpen(false)}
      />

      {/* Add manual model dialog */}
      <AddManualModelDialog
        providerName={decodedName}
        open={addModelOpen}
        onClose={() => setAddModelOpen(false)}
      />

      {/* Sync models confirmation */}
      <SyncModelsConfirmDialog
        providerName={decodedName}
        presetHint={provider.preset_name ?? undefined}
        open={syncOpen}
        onClose={() => setSyncOpen(false)}
      />
    </div>
  )
}
