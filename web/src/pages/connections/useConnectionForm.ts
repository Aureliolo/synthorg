import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  connectionTypeUsesWebhookReceipts,
  type Connection,
  type ConnectionType,
  type CreateConnectionRequest,
  type UpdateConnectionRequest,
} from '@/api/types/integrations'
import { useConnectionsStore } from '@/stores/connections'
import {
  type ConnectionFieldSpec,
  type ResolvedConnectionSpec,
  resolveConnectionSpec,
  validateA2APeerCredentials,
  validateConnectionField,
  validateConnectionName,
} from './connection-fields'

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
  const scheme = form.credentials['auth_scheme'] ?? 'api_key'
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
  spec: ResolvedConnectionSpec,
  mode: Mode,
): Record<string, string | null> {
  const dialect = form.type === 'database' ? (form.credentials['dialect'] ?? '') : undefined
  const next: Record<string, string | null> = {}
  if (mode === 'create') next['name'] = validateConnectionName(form.name)
  collectFieldErrors(spec.topLevelFields, form.topLevel, dialect, next)
  if (mode === 'create') {
    collectFieldErrors(spec.credentialFields, form.credentials, dialect, next)
    if (form.type === 'a2a_peer') applyA2APeerErrors(form, next)
  }
  return next
}

interface ResolvedCredentials {
  /** Non-secret credential fields sent inline. */
  readonly credentials: Record<string, string>
  /** Secret credential fields, captured out of band, as field -> handle. */
  readonly handles: Record<string, string>
}

type CaptureSecret = (
  draftId: string,
  field: string,
  value: string,
  secretKind: string,
) => Promise<string | null>

/**
 * Split credential fields into inline non-secret values and secret handles.
 * Each secret value is captured out of band (its raw form never enters the
 * create body); a capture failure toasts and aborts the submit (``null``).
 */
async function resolveCredentials(
  form: ConnectionFormState,
  spec: ResolvedConnectionSpec,
  draftId: string,
  captureSecret: CaptureSecret,
): Promise<ResolvedCredentials | null> {
  const credentials: Record<string, string> = {}
  const handles: Record<string, string> = {}
  for (const field of spec.credentialFields) {
    const raw = form.credentials[field.key]
    if (raw === undefined || raw === '') continue
    if (field.secret) {
      const handle = await captureSecret(draftId, field.key, raw, field.key)
      if (handle === null) return null
      handles[field.key] = handle
    } else {
      credentials[field.key] = raw
    }
  }
  return { credentials, handles }
}

interface WebhookRetention {
  readonly supportsWebhook: boolean
  readonly retentionValue: number | null
}

function buildCreateBody(
  form: ConnectionFormState,
  spec: ResolvedConnectionSpec,
  resolved: ResolvedCredentials,
  draftId: string,
  webhook: WebhookRetention,
): CreateConnectionRequest {
  const hasHandles = Object.keys(resolved.handles).length > 0
  return {
    name: form.name.trim(),
    connection_type: form.type as ConnectionType,
    auth_method: spec.defaultAuthMethod,
    credentials: resolved.credentials,
    credential_handles: resolved.handles,
    base_url: form.topLevel['base_url']?.trim() || null,
    health_check_enabled: true,
    sensitive: form.sensitive,
    ...(hasHandles ? { connection_draft_id: draftId } : {}),
    ...(webhook.supportsWebhook
      ? { webhook_receipt_retention_days: webhook.retentionValue }
      : {}),
  }
}

function buildUpdateBody(
  form: ConnectionFormState,
  supportsWebhook: boolean,
  retentionValue: number | null,
): UpdateConnectionRequest {
  return {
    base_url: form.topLevel['base_url']?.trim() || null,
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
  spec: ResolvedConnectionSpec
  mode: Mode
  connection: Connection | null | undefined
  draftId: string
  supportsWebhook: boolean
  retentionValue: number | null
  createConnection: (body: CreateConnectionRequest) => Promise<unknown>
  updateConnection: (name: string, body: UpdateConnectionRequest) => Promise<unknown>
  captureSecret: CaptureSecret
}

/** Persist via create or update; returns true when the dialog should close. */
async function submitConnection(deps: SubmitDeps): Promise<boolean> {
  if (deps.mode === 'create') {
    const resolved = await resolveCredentials(
      deps.form,
      deps.spec,
      deps.draftId,
      deps.captureSecret,
    )
    // A capture failure already toasted; keep the dialog open so the operator
    // can retry without losing the rest of the form.
    if (resolved === null) return false
    return Boolean(
      await deps.createConnection(
        buildCreateBody(deps.form, deps.spec, resolved, deps.draftId, {
          supportsWebhook: deps.supportsWebhook,
          retentionValue: deps.retentionValue,
        }),
      ),
    )
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
  spec: ResolvedConnectionSpec,
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
  handleFieldChange: (group: 'topLevel' | 'credentials', key: string, value: string) => void
  handleSubmit: (event: React.SyntheticEvent) => Promise<void>
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
      setErrors((p) => (p['name'] ? { ...p, name: null } : p))
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

  const handleSubmit = useCallback(
    async (event: React.SyntheticEvent) => {
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
        // A fresh capture-binding namespace per submit, generated in this
        // event handler (never during render), so secret handles cannot be
        // replayed across connection-setup attempts.
        draftId: crypto.randomUUID(),
        supportsWebhook: prep.supportsWebhook,
        retentionValue: prep.retentionValue,
        createConnection,
        updateConnection,
        captureSecret,
      })
      if (shouldClose) onClose()
    },
    [
      form,
      spec,
      mode,
      connection,
      createConnection,
      updateConnection,
      captureSecret,
      onClose,
    ],
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
