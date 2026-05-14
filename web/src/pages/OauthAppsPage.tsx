import { useMemo, useState } from 'react'
import { KeyRound, Plus } from 'lucide-react'
import { initiateOauth } from '@/api/endpoints/oauth'
import type { Connection } from '@/api/types/integrations'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ErrorBoundary } from '@/components/ui/error-boundary'
import { ListHeader } from '@/components/ui/list-header'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { useConnectionsData } from '@/hooks/useConnectionsData'
import { createLogger } from '@/lib/logger'
import { useConnectionsStore } from '@/stores/connections'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { ConnectionFormModal } from './connections/ConnectionFormModal'
import { ConnectionsSkeleton } from './connections/ConnectionsSkeleton'
import { OauthAppCard } from './oauth-apps/OauthAppCard'

const log = createLogger('oauth-apps-page')

type ModalState =
  | { kind: 'closed' }
  | { kind: 'create' }
  | { kind: 'edit'; connection: Connection }

export default function OauthAppsPage() {
  const { connections, loading, error } = useConnectionsData()
  const deleteConnection = useConnectionsStore((s) => s.deleteConnection)
  const [modal, setModal] = useState<ModalState>({ kind: 'closed' })
  const [pendingDelete, setPendingDelete] = useState<Connection | null>(null)

  const oauthApps = useMemo(
    () => connections.filter((c) => c.connection_type === 'oauth_app'),
    [connections],
  )

  const handleConnect = async (connection: Connection) => {
    try {
      const response = await initiateOauth({ connection_name: connection.name })
      useToastStore.getState().add({
        variant: 'info',
        title: 'OAuth flow started',
        description: 'Complete authorization in the new tab.',
      })
      window.open(response.authorization_url, '_blank', 'noopener,noreferrer')
    } catch (err) {
      log.warn('OAuth initiate failed:', getErrorMessage(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Failed to start OAuth flow',
        description: getErrorMessage(err),
      })
    }
  }

  const hasData = oauthApps.length > 0

  return (
    <div className="flex flex-col gap-section-gap">
      <ListHeader
        title="OAuth Apps"
        count={oauthApps.length}
        primaryAction={
          <Button size="sm" onClick={() => setModal({ kind: 'create' })}>
            <Plus className="mr-1.5 size-3.5" aria-hidden />
            Register app
          </Button>
        }
      />

      {error && (
        <ErrorBanner
          severity="error"
          title="Could not load OAuth apps"
          description={error}
        />
      )}

      {loading && !hasData ? (
        <ConnectionsSkeleton />
      ) : (
        <ErrorBoundary level="section">
          {hasData ? (
            <StaggerGroup className="grid grid-cols-2 gap-grid-gap max-[767px]:grid-cols-1">
              {oauthApps.map((conn) => (
                <StaggerItem key={conn.name}>
                  <OauthAppCard
                    connection={conn}
                    onEdit={() => setModal({ kind: 'edit', connection: conn })}
                    onDelete={() => setPendingDelete(conn)}
                    onConnect={() => void handleConnect(conn)}
                  />
                </StaggerItem>
              ))}
            </StaggerGroup>
          ) : (
            <EmptyState
              icon={KeyRound}
              title="No OAuth apps registered"
              description="Register an OAuth client app to reuse it across multiple connections."
              action={{
                label: 'Register app',
                onClick: () => setModal({ kind: 'create' }),
              }}
            />
          )}
        </ErrorBoundary>
      )}

      <ConnectionFormModal
        open={modal.kind !== 'closed'}
        mode={modal.kind === 'edit' ? 'edit' : 'create'}
        initialType="oauth_app"
        connection={modal.kind === 'edit' ? modal.connection : null}
        onClose={() => setModal({ kind: 'closed' })}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        title={`Delete ${pendingDelete?.name ?? ''}?`}
        description="This will permanently remove the OAuth app and its stored credentials."
        confirmLabel="Delete"
        variant="destructive"
        onOpenChange={(next) => {
          if (!next) setPendingDelete(null)
        }}
        onConfirm={async () => {
          if (pendingDelete) {
            await deleteConnection(pendingDelete.name)
            setPendingDelete(null)
          }
        }}
      />
    </div>
  )
}
