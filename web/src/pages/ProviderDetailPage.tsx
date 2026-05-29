import { useCallback, useState } from 'react'
import { useParams, useNavigate } from 'react-router'
import { useProviderDetailData } from '@/hooks/useProviderDetailData'
import { useProvidersData } from '@/hooks/useProvidersData'
import { useProvidersStore } from '@/stores/providers'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { DetailNavBar } from '@/components/ui/detail-nav-bar'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ErrorBanner } from '@/components/ui/error-banner'
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
import type { ProviderModelResponse } from '@/api/types/providers'

type ProviderDetailData = ReturnType<typeof useProviderDetailData>
type ProviderDetail = NonNullable<ProviderDetailData['provider']>
type ProviderNav = ReturnType<typeof useDetailNavigation>

interface ProviderDialogs {
  editOpen: boolean
  setEditOpen: (open: boolean) => void
  deleteOpen: boolean
  setDeleteOpen: (open: boolean) => void
  pullOpen: boolean
  setPullOpen: (open: boolean) => void
  configModel: ProviderModelResponse | null
  setConfigModel: (model: ProviderModelResponse | null) => void
  deleteModelId: string | null
  setDeleteModelId: (id: string | null) => void
  auditOpen: boolean
  setAuditOpen: (open: boolean) => void
  rateLimitsOpen: boolean
  setRateLimitsOpen: (open: boolean) => void
  rotateOpen: boolean
  setRotateOpen: (open: boolean) => void
  addModelOpen: boolean
  setAddModelOpen: (open: boolean) => void
  syncOpen: boolean
  setSyncOpen: (open: boolean) => void
  handleDeleteModelOpenChange: (open: boolean) => void
}

function useProviderDetailDialogs(): ProviderDialogs {
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

  const handleDeleteModelOpenChange = useCallback((open: boolean) => {
    if (!open) setDeleteModelId(null)
  }, [])

  return {
    editOpen, setEditOpen, deleteOpen, setDeleteOpen, pullOpen, setPullOpen,
    configModel, setConfigModel, deleteModelId, setDeleteModelId, auditOpen, setAuditOpen,
    rateLimitsOpen, setRateLimitsOpen, rotateOpen, setRotateOpen, addModelOpen, setAddModelOpen,
    syncOpen, setSyncOpen, handleDeleteModelOpenChange,
  }
}

function useProviderDetailNav(decodedName: string) {
  // Walk the parent provider list (filtered + sorted) so prev/next on
  // this detail page steps through the same providers the operator saw
  // on ProvidersPage. ``ProviderWithName.name`` is the URL key.
  const { filteredProviders } = useProvidersData()
  const routeForProvider = useCallback(
    (item: { id: string }) =>
      ROUTES.PROVIDER_DETAIL.replace(':providerName', encodeURIComponent(item.id)),
    [],
  )
  const navItems = filteredProviders.map((p) => ({ id: p.name }))
  const nav = useDetailNavigation({ items: navItems, currentId: decodedName, routeFor: routeForProvider })
  const { goPrev, goNext } = useDetailNavigationCallbacks(nav)
  return { nav, goPrev, goNext }
}

function ProviderDetailHeaderSection({
  provider,
  nav,
  goPrev,
  goNext,
  error,
}: {
  provider: ProviderDetail
  nav: ProviderNav
  goPrev: () => void
  goNext: () => void
  error: string | null
}) {
  return (
    <>
      <Breadcrumbs
        items={[{ label: 'Providers', to: ROUTES.PROVIDERS }, { label: provider.name }]}
      />
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
    </>
  )
}

function ProviderActionBar({ d }: { d: ProviderDialogs }) {
  return (
    <div className="flex flex-wrap gap-grid-gap">
      <Button variant="secondary" onClick={() => d.setAuditOpen(true)}>
        Audit log
      </Button>
      <Button variant="secondary" onClick={() => d.setRateLimitsOpen(true)}>
        Rate limits
      </Button>
      <Button variant="secondary" onClick={() => d.setRotateOpen(true)}>
        Rotate credentials
      </Button>
      <Button variant="secondary" onClick={() => d.setAddModelOpen(true)}>
        Add model manually
      </Button>
      <Button variant="secondary" onClick={() => d.setSyncOpen(true)}>
        Sync models
      </Button>
    </div>
  )
}

interface ProviderDetailBodyProps {
  d: ProviderDialogs
  provider: ProviderDetail
  health: ProviderDetailData['health']
  models: ProviderDetailData['models']
  decodedName: string
  testConnectionResult: ProviderDetailData['testConnectionResult']
  testingConnection: boolean
}

function ProviderDetailBody({
  d,
  provider,
  health,
  models,
  decodedName,
  testConnectionResult,
  testingConnection,
}: ProviderDetailBodyProps) {
  const discoveringModels = useProvidersStore((s) => s.discoveringModels)
  return (
    <>
      <ErrorBoundary level="section">
        <ProviderDetailHeader
          provider={provider}
          health={health}
          onEdit={() => d.setEditOpen(true)}
          onDelete={() => d.setDeleteOpen(true)}
          onTestConnection={() => {
            void useProvidersStore.getState().testConnection(decodedName)
          }}
          testingConnection={testingConnection}
          onRefresh={() => {
            void useProvidersStore.getState().discoverModels(
              decodedName,
              provider.preset_name ?? undefined,
            )
          }}
          refreshing={discoveringModels}
          onPullModel={() => d.setPullOpen(true)}
          supportsPull={provider.supports_model_pull}
        />
      </ErrorBoundary>

      <ProviderActionBar d={d} />

      {testConnectionResult && <TestConnectionResult result={testConnectionResult} />}

      {health && (
        <ErrorBoundary level="section">
          <ProviderHealthMetrics health={health} />
        </ErrorBoundary>
      )}

      <ErrorBoundary level="section">
        <ProviderModelList
          models={models}
          supportsDelete={provider.supports_model_delete}
          supportsConfig={provider.supports_model_config}
          onDelete={(modelId) => d.setDeleteModelId(modelId)}
          onConfigure={(model) => d.setConfigModel(model)}
        />
      </ErrorBoundary>
    </>
  )
}

function DeleteProviderModelDialog({
  d,
  decodedName,
}: {
  d: ProviderDialogs
  decodedName: string
}) {
  const deletingModel = useProvidersStore((s) => s.deletingModel)
  return (
    <ConfirmDialog
      open={d.deleteModelId !== null}
      onOpenChange={d.handleDeleteModelOpenChange}
      title="Delete Model"
      description={`Are you sure you want to delete "${d.deleteModelId ?? ''}" from this provider? This will remove the model from the local instance.`}
      variant="destructive"
      confirmLabel="Delete"
      loading={deletingModel}
      onConfirm={async () => {
        if (d.deleteModelId) {
          await useProvidersStore.getState().deleteModel(decodedName, d.deleteModelId)
        }
        d.setDeleteModelId(null)
      }}
    />
  )
}

function ProviderDetailDialogs({
  d,
  provider,
  decodedName,
  onNavigateAway,
}: {
  d: ProviderDialogs
  provider: ProviderDetail
  decodedName: string
  onNavigateAway: () => void
}) {
  return (
    <>
      <ProviderFormModal
        open={d.editOpen}
        onClose={() => d.setEditOpen(false)}
        mode="edit"
        provider={provider}
      />

      <ConfirmDialog
        open={d.deleteOpen}
        onOpenChange={d.setDeleteOpen}
        title="Delete Provider"
        description={`Are you sure you want to delete "${provider.name}"? This action cannot be undone.`}
        variant="destructive"
        confirmLabel="Delete"
        onConfirm={async () => {
          const success = await useProvidersStore.getState().deleteProvider(decodedName)
          if (success) onNavigateAway()
          d.setDeleteOpen(false)
        }}
      />

      <ModelPullDialog providerName={decodedName} open={d.pullOpen} onClose={() => d.setPullOpen(false)} />

      <ModelConfigDrawer
        providerName={decodedName}
        model={d.configModel}
        open={d.configModel !== null}
        onClose={() => d.setConfigModel(null)}
      />

      <DeleteProviderModelDialog d={d} decodedName={decodedName} />

      <AuditLogDrawer providerName={decodedName} open={d.auditOpen} onClose={() => d.setAuditOpen(false)} />

      <RateLimitsDrawer
        providerName={decodedName}
        open={d.rateLimitsOpen}
        onClose={() => d.setRateLimitsOpen(false)}
      />

      <CredentialsRotateDialog
        providerName={decodedName}
        provider={provider}
        open={d.rotateOpen}
        onClose={() => d.setRotateOpen(false)}
      />

      <AddManualModelDialog
        providerName={decodedName}
        open={d.addModelOpen}
        onClose={() => d.setAddModelOpen(false)}
      />

      <SyncModelsConfirmDialog
        providerName={decodedName}
        presetHint={provider.preset_name ?? undefined}
        open={d.syncOpen}
        onClose={() => d.setSyncOpen(false)}
      />
    </>
  )
}

export default function ProviderDetailPage() {
  const { providerName } = useParams<{ providerName: string }>()
  const navigate = useNavigate()
  const decodedName = providerName ?? ''

  const { provider, models, health, error, testConnectionResult, testingConnection } =
    useProviderDetailData(decodedName)
  const d = useProviderDetailDialogs()
  const { nav, goPrev, goNext } = useProviderDetailNav(decodedName)

  // A definitive negative answer from the backend always sets ``error``
  // (the store rewrites a missing-provider 404 into 'Provider not
  // found'); checked before the skeleton so a stale provider from a
  // prior detail view does not mask a fresh error.
  if (error && !provider) {
    return (
      <div className="flex flex-col gap-section-gap">
        <ErrorBanner severity="error" title="Could not load provider" description={error} />
      </div>
    )
  }

  // The page mounts with ``detailLoading=false`` because the polling
  // effect runs after the first render; show the skeleton whenever no
  // provider is resolved yet to avoid a "Provider not found" flash.
  if (!provider) {
    return <ProviderDetailSkeleton />
  }

  return (
    <div className="flex flex-col gap-section-gap">
      <ProviderDetailHeaderSection
        provider={provider}
        nav={nav}
        goPrev={goPrev}
        goNext={goNext}
        error={error}
      />
      <ProviderDetailBody
        d={d}
        provider={provider}
        health={health}
        models={models}
        decodedName={decodedName}
        testConnectionResult={testConnectionResult}
        testingConnection={testingConnection}
      />
      <ProviderDetailDialogs
        d={d}
        provider={provider}
        decodedName={decodedName}
        onNavigateAway={() => void navigate(ROUTES.PROVIDERS)}
      />
    </div>
  )
}
