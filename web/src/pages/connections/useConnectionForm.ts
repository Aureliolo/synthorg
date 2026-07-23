import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { type Connection, type ConnectionType } from '@/api/types/integrations'
import { useConnectionsStore } from '@/stores/connections'
import { type ResolvedConnectionSpec, resolveConnectionSpec } from './connection-fields'
import { type ConnectionFormState, type Mode } from './connection-form-state'
import { useConnectionSubmit } from './connection-submit'

export type { ConnectionFormState, Mode } from './connection-form-state'

/** The three form buckets a field's value can be written to. */
export type FieldGroup = 'topLevel' | 'credentials' | 'metadata'

const EMPTY_STATE: ConnectionFormState = {
  name: '',
  type: null,
  topLevel: {},
  credentials: {},
  metadata: {},
  webhookRetention: '',
  sensitive: false,
}

function makeInitialState(
  mode: Mode,
  initialType: ConnectionType | undefined,
  connection: Connection | null | undefined,
): ConnectionFormState {
  if (mode === 'edit' && connection) {
    return {
      name: connection.name,
      type: connection.connection_type,
      topLevel: { base_url: connection.base_url ?? '' },
      credentials: {},
      // Metadata lives on the record and stays editable, so hydrate it from
      // the connection (credentials, by contrast, are never re-surfaced).
      metadata: { ...connection.metadata },
      webhookRetention:
        connection.webhook_receipt_retention_days === null
          ? ''
          : String(connection.webhook_receipt_retention_days),
      sensitive: connection.sensitive,
    }
  }
  return { ...EMPTY_STATE, type: initialType ?? null }
}

interface ResetSnapshot {
  open: boolean
  connectionKey: string | null
  initialType: ConnectionType | null
  mode: Mode
}

function resetSnapshotChanged(prev: ResetSnapshot, next: ResetSnapshot): boolean {
  return (
    prev.open !== next.open ||
    prev.connectionKey !== next.connectionKey ||
    prev.initialType !== next.initialType ||
    prev.mode !== next.mode
  )
}

export interface ConnectionFormModalArgs {
  open: boolean
  mode: Mode
  initialType?: ConnectionType | undefined
  connection?: Connection | null | undefined
  onClose: () => void
}

export interface ConnectionForm {
  form: ConnectionFormState
  errors: Record<string, string | null>
  submitted: boolean
  spec: ResolvedConnectionSpec | null
  mutating: boolean
  setName: (value: string) => void
  setType: (type: ConnectionType) => void
  clearType: () => void
  setSensitive: (value: boolean) => void
  setWebhookRetention: (value: string) => void
  handleFieldChange: (group: FieldGroup, key: string, value: string) => void
  handleSubmit: (event: React.SyntheticEvent) => Promise<void>
}

interface ConnectionFieldSetters {
  setName: (value: string) => void
  setType: (type: ConnectionType) => void
  clearType: () => void
  setSensitive: (value: boolean) => void
  setWebhookRetention: (value: string) => void
  handleFieldChange: (group: FieldGroup, key: string, value: string) => void
}

/** Field-level setters that also clear the matching validation error. */
function useConnectionFieldSetters(
  setForm: React.Dispatch<React.SetStateAction<ConnectionFormState>>,
  setErrors: React.Dispatch<React.SetStateAction<Record<string, string | null>>>,
): ConnectionFieldSetters {
  const handleFieldChange = useCallback(
    (group: FieldGroup, key: string, value: string) => {
      setForm((p) => ({ ...p, [group]: { ...p[group], [key]: value } }))
      setErrors((p) => (p[key] ? { ...p, [key]: null } : p))
    },
    [setForm, setErrors],
  )
  const setName = useCallback(
    (value: string) => {
      setForm((p) => ({ ...p, name: value }))
      setErrors((p) => (p['name'] ? { ...p, name: null } : p))
    },
    [setForm, setErrors],
  )
  const setType = useCallback((type: ConnectionType) => setForm((p) => ({ ...p, type })), [setForm])
  const clearType = useCallback(
    () => setForm((p) => ({ ...p, type: null, topLevel: {}, credentials: {}, metadata: {} })),
    [setForm],
  )
  const setSensitive = useCallback(
    (value: boolean) => setForm((p) => ({ ...p, sensitive: value })),
    [setForm],
  )
  const setWebhookRetention = useCallback(
    (value: string) => {
      setForm((p) => ({ ...p, webhookRetention: value }))
      setErrors((p) =>
        p['webhook_receipt_retention_days'] ? { ...p, webhook_receipt_retention_days: null } : p,
      )
    },
    [setForm, setErrors],
  )
  return { setName, setType, clearType, setSensitive, setWebhookRetention, handleFieldChange }
}

export function useConnectionForm(props: ConnectionFormModalArgs): ConnectionForm {
  const { open, mode, initialType, connection, onClose } = props
  const mutating = useConnectionsStore((s) => s.mutating)
  const createConnection = useConnectionsStore((s) => s.createConnection)
  const updateConnection = useConnectionsStore((s) => s.updateConnection)
  const captureSecret = useConnectionsStore((s) => s.captureSecret)
  const connectionTypes = useConnectionsStore((s) => s.connectionTypes)
  const fetchConnectionTypes = useConnectionsStore((s) => s.fetchConnectionTypes)

  // Hydrate the connection-type registry the form renders from (idempotent;
  // pure API consumer, re-fetched on mount, never persisted client-side).
  useEffect(() => {
    void fetchConnectionTypes()
  }, [fetchConnectionTypes])

  const [form, setForm] = useState<ConnectionFormState>(() =>
    makeInitialState(mode, initialType, connection),
  )
  const [errors, setErrors] = useState<Record<string, string | null>>({})
  const [submitted, setSubmitted] = useState(false)

  // Reset the form when the modal opens or its identifying inputs change
  // while open (react.dev "Adjusting some state when a prop changes").
  // Compare the connection by its stable name rather than by object
  // identity so a re-fetched-but-equivalent connection object does not
  // wipe in-progress edits.
  const connectionKey = connection?.name ?? null
  const snapshot: ResetSnapshot = { open, connectionKey, initialType: initialType ?? null, mode }
  const prevSnapshotRef = useRef<ResetSnapshot>(snapshot)
  const inputsChanged = resetSnapshotChanged(prevSnapshotRef.current, snapshot)
  prevSnapshotRef.current = snapshot
  if (open && inputsChanged) {
    setForm(makeInitialState(mode, initialType, connection))
    setErrors({})
    setSubmitted(false)
  }

  const spec = useMemo<ResolvedConnectionSpec | null>(() => {
    if (!form.type) return null
    const meta = connectionTypes.find((t) => t.connection_type === form.type)
    return meta ? resolveConnectionSpec(meta) : null
  }, [form.type, connectionTypes])
  const setters = useConnectionFieldSetters(setForm, setErrors)

  const { handleSubmit, submitting } = useConnectionSubmit({
    form,
    spec,
    mode,
    connection,
    createConnection,
    updateConnection,
    captureSecret,
    onClose,
    setSubmitted,
    setErrors,
  })

  return {
    form,
    errors,
    submitted,
    spec,
    // Surface the capture phase through the same flag the submit button already
    // reads, so it stays disabled across the whole submit (capture + create),
    // not just the store's create/update window.
    mutating: mutating || submitting,
    ...setters,
    handleSubmit,
  }
}
