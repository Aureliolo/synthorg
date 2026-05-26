import { useCallback, useMemo, useRef, useState } from 'react'
import {
  connectionTypeUsesWebhookReceipts,
  type Connection,
  type ConnectionType,
  type CreateConnectionRequest,
  type UpdateConnectionRequest,
} from '@/api/types/integrations'
import { useConnectionsStore } from '@/stores/connections'
import {
  CONNECTION_TYPE_FIELDS,
  type ConnectionFieldSpec,
  type ConnectionTypeSpec,
  validateA2APeerCredentials,
  validateConnectionField,
  validateConnectionName,
} from './connection-type-fields'

export type Mode = 'create' | 'edit'

export interface ConnectionFormState {
  name: string
  type: ConnectionType | null
  topLevel: Record<string, string>
  credentials: Record<string, string>
  webhookRetention: string
  sensitive: boolean
}

const EMPTY_STATE: ConnectionFormState = {
  name: '',
  type: null,
  topLevel: {},
  credentials: {},
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
      webhookRetention:
        connection.webhook_receipt_retention_days === null
          ? ''
          : String(connection.webhook_receipt_retention_days),
      sensitive: connection.sensitive,
    }
  }
  return { ...EMPTY_STATE, type: initialType ?? null }
}

type RetentionResult = { ok: true; value: number | null } | { ok: false; error: string }

function parseRetentionDays(raw: string): RetentionResult {
  const trimmed = raw.trim()
  if (trimmed === '') return { ok: true, value: null }
  if (!/^\d+$/.test(trimmed)) return { ok: false, error: 'Must be a non-negative integer or blank' }
  const parsed = Number.parseInt(trimmed, 10)
  if (!Number.isFinite(parsed) || parsed < 0) {
    return { ok: false, error: 'Must be a non-negative integer or blank' }
  }
  return { ok: true, value: parsed }
}

function applyA2APeerErrors(form: ConnectionFormState, next: Record<string, string | null>): void {
  const scheme = form.credentials.auth_scheme ?? 'api_key'
  const schemeErrors = validateA2APeerCredentials(scheme, form.credentials)
  for (const [key, msg] of Object.entries(schemeErrors)) {
    const errorKey = key === '_scheme' ? 'auth_scheme' : key
    if (!next[errorKey]) next[errorKey] = msg
  }
}

function collectFieldErrors(
  fields: readonly ConnectionFieldSpec[],
  values: Record<string, string>,
  dialect: string | undefined,
  into: Record<string, string | null>,
): void {
  for (const field of fields) {
    into[field.key] = validateConnectionField(field, values[field.key] ?? '', dialect)
  }
}

function validateConnectionForm(
  form: ConnectionFormState,
  spec: ConnectionTypeSpec,
  mode: Mode,
): Record<string, string | null> {
  const dialect = form.type === 'database' ? (form.credentials.dialect ?? '') : undefined
  const next: Record<string, string | null> = {}
  if (mode === 'create') next.name = validateConnectionName(form.name)
  collectFieldErrors(spec.topLevelFields, form.topLevel, dialect, next)
  if (mode === 'create') {
    collectFieldErrors(spec.credentialFields, form.credentials, dialect, next)
    if (form.type === 'a2a_peer') applyA2APeerErrors(form, next)
  }
  return next
}

function buildCreateBody(
  form: ConnectionFormState,
  spec: ConnectionTypeSpec,
  supportsWebhook: boolean,
  retentionValue: number | null,
): CreateConnectionRequest {
  const credentials: Record<string, string> = {}
  for (const field of spec.credentialFields) {
    const value = form.credentials[field.key]
    if (value !== undefined && value !== '') credentials[field.key] = value
  }
  return {
    name: form.name.trim(),
    connection_type: form.type as ConnectionType,
    auth_method: spec.defaultAuthMethod,
    credentials,
    base_url: form.topLevel.base_url?.trim() || null,
    health_check_enabled: true,
    sensitive: form.sensitive,
    ...(supportsWebhook ? { webhook_receipt_retention_days: retentionValue } : {}),
  }
}

function buildUpdateBody(
  form: ConnectionFormState,
  supportsWebhook: boolean,
  retentionValue: number | null,
): UpdateConnectionRequest {
  return {
    base_url: form.topLevel.base_url?.trim() || null,
    sensitive: form.sensitive,
    ...(supportsWebhook ? { webhook_receipt_retention_days: retentionValue } : {}),
  }
}

interface SnapshotInputs {
  open: boolean
  connection: Connection | null
  initialType: ConnectionType | null
  mode: Mode
}

function didInputsChange(prev: SnapshotInputs, curr: SnapshotInputs): boolean {
  return (
    prev.open !== curr.open ||
    prev.connection !== curr.connection ||
    prev.initialType !== curr.initialType ||
    prev.mode !== curr.mode
  )
}

interface SubmitDeps {
  form: ConnectionFormState
  spec: ConnectionTypeSpec
  mode: Mode
  connection: Connection | null | undefined
  supportsWebhook: boolean
  retentionValue: number | null
  createConnection: (body: CreateConnectionRequest) => Promise<unknown>
  updateConnection: (name: string, body: UpdateConnectionRequest) => Promise<unknown>
}

/** Persist via create or update; returns true when the dialog should close. */
async function submitConnection(deps: SubmitDeps): Promise<boolean> {
  if (deps.mode === 'create') {
    return Boolean(await deps.createConnection(buildCreateBody(deps.form, deps.spec, deps.supportsWebhook, deps.retentionValue)))
  }
  if (deps.connection) {
    return Boolean(
      await deps.updateConnection(deps.connection.name, buildUpdateBody(deps.form, deps.supportsWebhook, deps.retentionValue)),
    )
  }
  return false
}

export interface ConnectionFormModalArgs {
  open: boolean
  mode: Mode
  initialType?: ConnectionType
  connection?: Connection | null
  onClose: () => void
}

export interface ConnectionForm {
  form: ConnectionFormState
  errors: Record<string, string | null>
  submitted: boolean
  spec: ConnectionTypeSpec | null
  mutating: boolean
  setName: (value: string) => void
  setType: (type: ConnectionType) => void
  clearType: () => void
  setSensitive: (value: boolean) => void
  setWebhookRetention: (value: string) => void
  handleFieldChange: (group: 'topLevel' | 'credentials', key: string, value: string) => void
  handleSubmit: (event: React.FormEvent) => Promise<void>
}

export function useConnectionForm(props: ConnectionFormModalArgs): ConnectionForm {
  const { open, mode, initialType, connection, onClose } = props
  const mutating = useConnectionsStore((s) => s.mutating)
  const createConnection = useConnectionsStore((s) => s.createConnection)
  const updateConnection = useConnectionsStore((s) => s.updateConnection)

  const [form, setForm] = useState<ConnectionFormState>(() =>
    makeInitialState(mode, initialType, connection),
  )
  const [errors, setErrors] = useState<Record<string, string | null>>({})
  const [submitted, setSubmitted] = useState(false)

  // Render-phase reset on open transition / changed inputs while open.
  const current: SnapshotInputs = { open, connection: connection ?? null, initialType: initialType ?? null, mode }
  const prevRef = useRef(current)
  if (open && didInputsChange(prevRef.current, current)) {
    setForm(makeInitialState(mode, initialType, connection))
    setErrors({})
    setSubmitted(false)
  }
  prevRef.current = current

  const spec = useMemo(() => (form.type ? CONNECTION_TYPE_FIELDS[form.type] : null), [form.type])

  const handleFieldChange = useCallback(
    (group: 'topLevel' | 'credentials', key: string, value: string) => {
      setForm((p) => ({ ...p, [group]: { ...p[group], [key]: value } }))
      setErrors((p) => (p[key] ? { ...p, [key]: null } : p))
    },
    [],
  )

  const handleSubmit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault()
      setSubmitted(true)
      if (!spec || !form.type) return
      const nextErrors = validateConnectionForm(form, spec, mode)
      setErrors(nextErrors)
      if (!Object.values(nextErrors).every((v) => v === null)) return

      const supportsWebhook = connectionTypeUsesWebhookReceipts(form.type)
      const retention: RetentionResult = supportsWebhook
        ? parseRetentionDays(form.webhookRetention)
        : { ok: true, value: null }
      if (!retention.ok) {
        setErrors((p) => ({ ...p, webhook_receipt_retention_days: retention.error }))
        return
      }

      const shouldClose = await submitConnection({
        form,
        spec,
        mode,
        connection,
        supportsWebhook,
        retentionValue: retention.value,
        createConnection,
        updateConnection,
      })
      if (shouldClose) onClose()
    },
    [form, spec, mode, connection, createConnection, updateConnection, onClose],
  )

  return {
    form,
    errors,
    submitted,
    spec,
    mutating,
    setName: useCallback((value: string) => {
      setForm((p) => ({ ...p, name: value }))
      setErrors((p) => (p.name ? { ...p, name: null } : p))
    }, []),
    setType: useCallback((type: ConnectionType) => setForm((p) => ({ ...p, type })), []),
    clearType: useCallback(() => setForm((p) => ({ ...p, type: null, topLevel: {}, credentials: {} })), []),
    setSensitive: useCallback((value: boolean) => setForm((p) => ({ ...p, sensitive: value })), []),
    setWebhookRetention: useCallback((value: string) => {
      setForm((p) => ({ ...p, webhookRetention: value }))
      setErrors((p) =>
        p.webhook_receipt_retention_days ? { ...p, webhook_receipt_retention_days: null } : p,
      )
    }, []),
    handleFieldChange,
    handleSubmit,
  }
}
