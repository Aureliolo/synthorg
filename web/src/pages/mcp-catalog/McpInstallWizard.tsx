import { useEffect, useMemo, useRef } from 'react'
import { AlertCircle, CheckCircle2, Loader2 } from 'lucide-react'
import type { Connection, McpCatalogEntry } from '@/api/types/integrations'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogCloseButton,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { SelectField } from '@/components/ui/select-field'
import { useConnectionsStore } from '@/stores/connections'
import { useMcpCatalogStore } from '@/stores/mcp-catalog'

export interface McpInstallWizardProps {
  onRequestCreateConnection: (initialType: Connection['connection_type']) => void
}

export function McpInstallWizard({ onRequestCreateConnection }: McpInstallWizardProps) {
  const flow = useMcpCatalogStore((s) => s.installFlow)
  const context = useMcpCatalogStore((s) => s.installContext)
  const entries = useMcpCatalogStore((s) => s.entries)
  const confirmInstall = useMcpCatalogStore((s) => s.confirmInstall)
  const setConnection = useMcpCatalogStore((s) => s.setInstallConnection)
  const resetInstall = useMcpCatalogStore((s) => s.resetInstall)
  const connections = useConnectionsStore((s) => s.connections)

  const entry: McpCatalogEntry | null = useMemo(
    () => entries.find((e) => e.id === context.entryId) ?? null,
    [entries, context.entryId],
  )

  const requiredType = entry?.required_connection_type ?? null
  const eligibleConnections = useMemo<readonly Connection[]>(
    () =>
      requiredType
        ? connections.filter((c) => c.connection_type === requiredType)
        : [],
    [connections, requiredType],
  )

  // Track whether we've already auto-dispatched confirmInstall for
  // this install session. Without this guard, the Retry button on a
  // connectionless entry would set flow='installing', re-triggering
  // the effect and firing a second parallel install request.
  const autoConfirmedRef = useRef(false)

  useEffect(() => {
    if (flow === 'installing' && requiredType === null && !autoConfirmedRef.current) {
      autoConfirmedRef.current = true
      void confirmInstall()
    }
    if (flow === 'idle') {
      autoConfirmedRef.current = false
    }
  }, [flow, requiredType, confirmInstall])

  if (flow === 'idle') {
    return null
  }

  const isOpen = true
  const handleClose = () => resetInstall()

  if (entry === null && flow === 'error') {
    return (
      <Dialog open={isOpen} onOpenChange={(next) => !next && handleClose()}>
        <DialogContent className="sm:max-w-dialog-compact">
          <DialogHeader>
            <DialogTitle>Install failed</DialogTitle>
            <DialogCloseButton />
          </DialogHeader>
          <div className="p-card">
            <ErrorStep
              message={context.errorMessage ?? 'Catalog entry not found'}
              onRetry={handleClose}
              onCancel={handleClose}
            />
          </div>
        </DialogContent>
      </Dialog>
    )
  }

  if (entry === null) {
    return null
  }

  return (
    <Dialog open={isOpen} onOpenChange={(next) => !next && handleClose()}>
      <DialogContent className="sm:max-w-dialog-compact">
        <DialogHeader>
          <DialogTitle>Install {entry.name}</DialogTitle>
          <DialogCloseButton />
        </DialogHeader>

        <div className="p-card">
          {flow === 'picking-connection' && requiredType !== null && (
            <PickConnectionStep
              requiredType={requiredType}
              connections={eligibleConnections}
              selected={context.connectionName}
              onSelect={setConnection}
              onCreate={() => onRequestCreateConnection(requiredType)}
              onCancel={handleClose}
              onConfirm={() => void confirmInstall()}
            />
          )}

          {flow === 'installing' && <InstallingStep />}

          {flow === 'done' && context.result && (
            <DoneStep
              serverName={context.result.server_name}
              toolCount={context.result.tool_count}
              onClose={handleClose}
            />
          )}

          {flow === 'error' && (
            <ErrorStep
              message={context.errorMessage ?? 'Unknown error'}
              onRetry={() => void confirmInstall()}
              onCancel={handleClose}
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

interface PickConnectionStepProps {
  requiredType: Connection['connection_type']
  connections: readonly Connection[]
  selected: string | null
  onSelect: (name: string | null) => void
  onCreate: () => void
  onCancel: () => void
  onConfirm: () => void
}

function PickConnectionStep({
  requiredType,
  connections,
  selected,
  onSelect,
  onCreate,
  onCancel,
  onConfirm,
}: PickConnectionStepProps) {
  const options = [
    { value: '', label: '-- Select a connection --' },
    ...connections.map((c) => ({ value: c.name, label: c.name })),
  ]

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-text-secondary">
        This MCP server requires a{' '}
        <span className="font-medium text-foreground">
          {requiredType.replaceAll('_', ' ')}
        </span>{' '}
        connection. Pick an existing one, or create a new connection.
      </p>

      {connections.length > 0 ? (
        <SelectField
          label="Connection"
          options={options}
          value={selected ?? ''}
          onChange={(value) => onSelect(value || null)}
        />
      ) : (
        <p className="rounded-md bg-surface p-card text-xs text-text-muted">
          No eligible connections found. Create one first.
        </p>
      )}

      <div className="flex flex-wrap justify-between gap-2">
        <Button type="button" variant="ghost" onClick={onCreate}>
          Create new connection
        </Button>
        <div className="flex gap-2">
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            type="button"
            onClick={onConfirm}
            disabled={selected === null || selected === ''}
          >
            Install
          </Button>
        </div>
      </div>
    </div>
  )
}

function InstallingStep() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-8">
      <Loader2 className="size-8 animate-spin text-accent" aria-hidden />
      <p className="text-sm text-text-secondary">Installing MCP server...</p>
    </div>
  )
}

function DoneStep({
  serverName,
  toolCount,
  onClose,
}: {
  serverName: string
  toolCount: number
  onClose: () => void
}) {
  return (
    <div className="flex flex-col items-center gap-4 py-4">
      <CheckCircle2 className="size-12 text-success" aria-hidden />
      <div className="text-center">
        <p className="text-base font-semibold text-foreground">
          {serverName} installed
        </p>
        <p className="mt-1 text-sm text-text-secondary">
          {toolCount} tool{toolCount === 1 ? '' : 's'} available after the next
          MCP bridge reload.
        </p>
      </div>
      <Button type="button" onClick={onClose}>
        Done
      </Button>
    </div>
  )
}

function ErrorStep({
  message,
  onRetry,
  onCancel,
}: {
  message: string
  onRetry: () => void
  onCancel: () => void
}) {
  return (
    <div className="flex flex-col items-center gap-4 py-4">
      <AlertCircle className="size-12 text-danger" aria-hidden />
      <p className="text-center text-sm text-text-secondary">{message}</p>
      <div className="flex gap-2">
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="button" onClick={onRetry}>
          Retry
        </Button>
      </div>
    </div>
  )
}
