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

interface PreparedSubmit {
  errors: Record<string, string | null>
  proceed: boolean
  supportsWebhook: boolean
  retentionValue: number | null
  retentionError: string | null
}

/** Validate the form and resolve webhook retention without touching state. */
function prepareConnectionSubmit(
  form: ConnectionFormState,
  spec: ConnectionTypeSpec,
  mode: Mode,
): PreparedSubmit {
  const errors = validateConnectionForm(form, spec, mode)
  const base = { errors, proceed: false, supportsWebhook: false, retentionValue: null, retentionError: null }
  if (!Object.values(errors).every((v) => v === null)) return base
  const supportsWebhook = form.type ? connectionTypeUsesWebhookReceipts(form.type) : false
  const retention: RetentionResult = supportsWebhook
    ? parseRetentionDays(form.webhookRetention)
    : { ok: true, value: null }
  if (!retention.ok) return { ...base, supportsWebhook, retentionError: retention.error }
  return { errors, proceed: true, supportsWebhook, retentionValue: retention.value, retentionError: null }
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

interface ConnectionFieldSetters {
  setName: (value: string) => void
  setType: (type: ConnectionType) => void
  clearType: () => void
  setSensitive: (value: boolean) => void
  setWebhookRetention: (value: string) => void
  handleFieldChange: (group: 'topLevel' | 'credentials', key: string, value: string) => void
}

/** Field-level setters that also clear the matching validation error. */
function useConnectionFieldSetters(
  setForm: React.Dispatch<React.SetStateAction<ConnectionFormState>>,
  setErrors: React.Dispatch<React.SetStateAction<Record<string, string | null>>>,
): ConnectionFieldSetters {
  const handleFieldChange = useCallback(
    (group: 'topLevel' | 'credentials', key: string, value: string) => {
      setForm((p) => ({ ...p, [group]: { ...p[group], [key]: value } }))
      setErrors((p) => (p[key] ? { ...p, [key]: null } : p))
    },
    [setForm, setErrors],
  )
  const setName = useCallback(
    (value: string) => {
      setForm((p) => ({ ...p, name: value }))
      setErrors((p) => (p.name ? { ...p, name: null } : p))
    },
    [setForm, setErrors],
  )
  const setType = useCallback((type: ConnectionType) => setForm((p) => ({ ...p, type })), [setForm])
  const clearType = useCallback(
    () => setForm((p) => ({ ...p, type: null, topLevel: {}, credentials: {} })),
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
        p.webhook_receipt_retention_days ? { ...p, webhook_receipt_retention_days: null } : p,
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

  const spec = useMemo(() => (form.type ? CONNECTION_TYPE_FIELDS[form.type] : null), [form.type])
  const setters = useConnectionFieldSetters(setForm, setErrors)

  const handleSubmit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault()
      setSubmitted(true)
      if (!spec || !form.type) return
      const prep = prepareConnectionSubmit(form, spec, mode)
      setErrors(
        prep.retentionError
          ? { ...prep.errors, webhook_receipt_retention_days: prep.retentionError }
          : prep.errors,
      )
      if (!prep.proceed) return
      const shouldClose = await submitConnection({
        form,
        spec,
        mode,
        connection,
        supportsWebhook: prep.supportsWebhook,
        retentionValue: prep.retentionValue,
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
    ...setters,
    handleSubmit,
  }
}
