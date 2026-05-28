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
      requiredType ? connections.filter((c) => c.connection_type === requiredType) : [],
    [connections, requiredType],
  )

  useAutoConfirmConnectionlessInstall(flow, requiredType, confirmInstall)

  if (flow === 'idle') return null
  const handleClose = () => resetInstall()

  if (entry === null && flow === 'error') {
    return (
      <WizardDialog title="Install failed" onClose={handleClose}>
        <ErrorStep
          message={context.errorMessage ?? 'Catalog entry not found'}
          onRetry={handleClose}
          onCancel={handleClose}
        />
      </WizardDialog>
    )
  }

  if (entry === null) return null

  return (
    <WizardDialog title={`Install ${entry.name}`} onClose={handleClose}>
      <InstallWizardBody
        flow={flow}
        requiredType={requiredType}
        eligibleConnections={eligibleConnections}
        context={context}
        onSelect={setConnection}
        onCreateConnection={() => requiredType && onRequestCreateConnection(requiredType)}
        onConfirm={() => void confirmInstall()}
        onClose={handleClose}
      />
    </WizardDialog>
  )
}

function useAutoConfirmConnectionlessInstall(
  flow: ReturnType<typeof useMcpCatalogStore.getState>['installFlow'],
  requiredType: Connection['connection_type'] | null,
  confirmInstall: ReturnType<typeof useMcpCatalogStore.getState>['confirmInstall'],
): void {
  // Track whether we've already auto-dispatched confirmInstall for this
  // install session. Without this guard, the Retry button on a connectionless
  // entry would set flow='installing', re-triggering the effect and firing a
  // second parallel install request.
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
}

interface WizardDialogProps {
  title: string
  onClose: () => void
  children: React.ReactNode
}

function WizardDialog({ title, onClose, children }: WizardDialogProps) {
  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-dialog-compact">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogCloseButton />
        </DialogHeader>
        <div className="p-card">{children}</div>
      </DialogContent>
    </Dialog>
  )
}

interface InstallWizardBodyProps {
  flow: ReturnType<typeof useMcpCatalogStore.getState>['installFlow']
  requiredType: Connection['connection_type'] | null
  eligibleConnections: readonly Connection[]
  context: ReturnType<typeof useMcpCatalogStore.getState>['installContext']
  onSelect: (name: string | null) => void
  onCreateConnection: () => void
  onConfirm: () => void
  onClose: () => void
}

function InstallWizardBody({
  flow,
  requiredType,
  eligibleConnections,
  context,
  onSelect,
  onCreateConnection,
  onConfirm,
  onClose,
}: InstallWizardBodyProps) {
  if (flow === 'picking-connection' && requiredType !== null) {
    return (
      <PickConnectionStep
        requiredType={requiredType}
        connections={eligibleConnections}
        selected={context.connectionName}
        onSelect={onSelect}
        onCreate={onCreateConnection}
        onCancel={onClose}
        onConfirm={onConfirm}
      />
    )
  }
  if (flow === 'installing') return <InstallingStep />
  if (flow === 'done' && context.result) {
    return (
      <DoneStep
        serverName={context.result.server_name}
        toolCount={context.result.tool_count}
        onClose={onClose}
      />
    )
  }
  if (flow === 'error') {
    return (
      <ErrorStep
        message={context.errorMessage ?? 'Unknown error'}
        onRetry={onConfirm}
        onCancel={onClose}
      />
    )
  }
  return null
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
  const confirmDisabled = selected === null || selected === ''

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
          <Button type="button" onClick={onConfirm} disabled={confirmDisabled}>
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

interface DoneStepProps {
  serverName: string
  toolCount: number
  onClose: () => void
}

function DoneStep({ serverName, toolCount, onClose }: DoneStepProps) {
  return (
    <div className="flex flex-col items-center gap-4 py-4">
      <CheckCircle2 className="size-12 text-success" aria-hidden />
      <div className="text-center">
        <p className="text-base font-semibold text-foreground">{serverName} installed</p>
        <p className="mt-1 text-sm text-text-secondary">
          {toolCount} tool{toolCount === 1 ? '' : 's'} available after the next MCP bridge
          reload.
        </p>
      </div>
      <Button type="button" onClick={onClose}>
        Done
      </Button>
    </div>
  )
}

interface ErrorStepProps {
  message: string
  onRetry: () => void
  onCancel: () => void
}

function ErrorStep({ message, onRetry, onCancel }: ErrorStepProps) {
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
