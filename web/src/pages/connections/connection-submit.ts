import { useCallback, useRef, useState, type SyntheticEvent } from 'react'
import {
  connectionTypeUsesWebhookReceipts,
  type Connection,
  type ConnectionType,
  type CreateConnectionRequest,
  type UpdateConnectionRequest,
} from '@/api/types/integrations'
import {
  type ConnectionFieldSpec,
  type ResolvedConnectionSpec,
  isFieldVisible,
  validateConnectionField,
  validateConnectionName,
} from './connection-fields'
import type { ConnectionFormState, Mode } from './connection-form-state'

/**
 * Form validation + submit machinery for the connection form, split out of
 * ``useConnectionForm`` so both stay within their size budgets. Exposes the
 * ``useConnectionSubmit`` hook the form consumes.
 */

type RetentionResult = { ok: true; value: number | null } | { ok: false; error: string }

const FORGE_CONNECTION_TYPES = new Set<ConnectionType>([
  'github',
  'gitlab',
  'gitea',
  'forgejo',
])

/** Whether a connection type has a forge repository scope. */
export function isForgeConnectionType(type: ConnectionType | null): boolean {
  return type !== null && FORGE_CONNECTION_TYPES.has(type)
}

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

/**
 * Every field's current value, whatever its placement.
 *
 * A field's condition can point at one in another bucket (a metadata-placed
 * vendor governing the top-level base URL), so conditions are evaluated
 * against the whole form rather than the bucket a field happens to sit in.
 */
const formValuesCache = new WeakMap<ConnectionFormState, Readonly<Record<string, string>>>()

export function allFormValues(form: ConnectionFormState): Readonly<Record<string, string>> {
  // Form state is replaced wholesale on every edit, so its identity is a
  // sound cache key: a single submit otherwise merges the same three buckets
  // once per validate/metadata/base-url/credential pass. Frozen because the
  // result is now shared between those callers.
  const cached = formValuesCache.get(form)
  if (cached) return cached
  const merged = Object.freeze({ ...form.topLevel, ...form.metadata, ...form.credentials })
  formValuesCache.set(form, merged)
  return merged
}

function collectFieldErrors(
  fields: readonly ConnectionFieldSpec[],
  values: Record<string, string>,
  all: Readonly<Record<string, string>>,
  into: Record<string, string | null>,
): void {
  for (const field of fields) {
    into[field.key] = validateConnectionField(field, values[field.key] ?? '', all)
  }
}

function validateConnectionForm(
  form: ConnectionFormState,
  spec: ResolvedConnectionSpec,
  mode: Mode,
): Record<string, string | null> {
  const all = allFormValues(form)
  const next: Record<string, string | null> = {}
  if (mode === 'create') next['name'] = validateConnectionName(form.name)
  collectFieldErrors(spec.topLevelFields, form.topLevel, all, next)
  // Metadata lives on the record and is editable in both modes, so validate
  // it whether creating or editing (credentials only exist at create time).
  collectFieldErrors(spec.metadataFields, form.metadata, all, next)
  if (mode === 'create') {
    collectFieldErrors(spec.credentialFields, form.credentials, all, next)
  }
  return next
}

/**
 * Collect the non-blank metadata field values into the record's metadata map.
 * Only fields the type actually declares and currently shows are sent, so
 * neither a stale value from a previously selected type nor one typed before
 * a condition hid the field leaks onto the connection.
 */
function collectMetadata(
  form: ConnectionFormState,
  spec: ResolvedConnectionSpec,
): Record<string, string> {
  const all = allFormValues(form)
  const metadata: Record<string, string> = {}
  for (const field of spec.metadataFields) {
    if (!isFieldVisible(field, all)) continue
    const raw = form.metadata[field.key]?.trim()
    if (raw) metadata[field.key] = raw
  }
  return metadata
}

/**
 * The base URL to submit, or ``null`` when the field does not currently apply.
 *
 * A vendor preset supplies its own endpoint and hides the field; a URL typed
 * before that switch must not ride along, or the operator would silently
 * override the preset with a stale value.
 */
function submittedBaseUrl(
  form: ConnectionFormState,
  spec: ResolvedConnectionSpec,
): string | null {
  const all = allFormValues(form)
  const field = spec.topLevelFields.find((f) => f.key === 'base_url')
  if (field && !isFieldVisible(field, all)) return null
  return form.topLevel['base_url']?.trim() || null
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
  const all = allFormValues(form)
  const credentials: Record<string, string> = {}
  const handles: Record<string, string> = {}
  for (const field of spec.credentialFields) {
    if (!isFieldVisible(field, all)) continue
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

interface CreateBodyContext extends WebhookRetention {
  readonly draftId: string
}

function buildCreateBody(
  form: ConnectionFormState,
  connectionType: ConnectionType,
  spec: ResolvedConnectionSpec,
  resolved: ResolvedCredentials,
  ctx: CreateBodyContext,
): CreateConnectionRequest {
  const hasHandles = Object.keys(resolved.handles).length > 0
  return {
    name: form.name.trim(),
    connection_type: connectionType,
    auth_method: spec.defaultAuthMethod,
    credentials: resolved.credentials,
    credential_handles: resolved.handles,
    base_url: submittedBaseUrl(form, spec),
    metadata: collectMetadata(form, spec),
    health_check_enabled: true,
    sensitive: form.sensitive,
    // Always sent (non-forge types carry an empty, deny-all scope); the create
    // schema requires the field even though it defaults empty.
    allowed_repos: isForgeConnectionType(connectionType) ? form.allowedRepos : [],
    ...(hasHandles ? { connection_draft_id: ctx.draftId } : {}),
    ...(ctx.supportsWebhook
      ? { webhook_receipt_retention_days: ctx.retentionValue }
      : {}),
  }
}

function buildUpdateBody(
  form: ConnectionFormState,
  spec: ResolvedConnectionSpec,
  supportsWebhook: boolean,
  retentionValue: number | null,
): UpdateConnectionRequest {
  return {
    base_url: submittedBaseUrl(form, spec),
    metadata: collectMetadata(form, spec),
    sensitive: form.sensitive,
    ...(supportsWebhook ? { webhook_receipt_retention_days: retentionValue } : {}),
    ...(isForgeConnectionType(form.type)
      ? { allowed_repos: form.allowedRepos }
      : {}),
  }
}

interface SubmitDeps {
  form: ConnectionFormState
  connectionType: ConnectionType
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
        buildCreateBody(deps.form, deps.connectionType, deps.spec, resolved, {
          draftId: deps.draftId,
          supportsWebhook: deps.supportsWebhook,
          retentionValue: deps.retentionValue,
        }),
      ),
    )
  }
  if (deps.connection) {
    return Boolean(
      await deps.updateConnection(
        deps.connection.name,
        buildUpdateBody(deps.form, deps.spec, deps.supportsWebhook, deps.retentionValue),
      ),
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

export interface UseSubmitArgs {
  form: ConnectionFormState
  spec: ResolvedConnectionSpec | null
  mode: Mode
  connection: Connection | null | undefined
  createConnection: (body: CreateConnectionRequest) => Promise<unknown>
  updateConnection: (name: string, body: UpdateConnectionRequest) => Promise<unknown>
  captureSecret: CaptureSecret
  onClose: () => void
  setSubmitted: (value: boolean) => void
  setErrors: (errors: Record<string, string | null>) => void
}

/**
 * Owns the submit handler and its in-flight guard. ``submitting`` covers the
 * whole submit including the sequential secret-capture round-trips that run
 * before ``createConnection`` (the store's ``mutating`` flag only spans the
 * create/update call), so a double click/Enter cannot fire duplicate captures
 * or a duplicate create.
 */
export function useConnectionSubmit(args: UseSubmitArgs): {
  handleSubmit: (event: SyntheticEvent) => Promise<void>
  submitting: boolean
} {
  const { form, spec, mode, connection, onClose, setSubmitted, setErrors } = args
  const { createConnection, updateConnection, captureSecret } = args
  const [submitting, setSubmitting] = useState(false)
  // Synchronous guard against a double-fire: `submitting` state updates async,
  // so two rapid submit events could both pass a state-based check before the
  // re-render. The ref flips synchronously, blocking the second event; the
  // `submitting` state is kept only to drive the disabled UI.
  const inFlightRef = useRef(false)
  const handleSubmit = useCallback(
    async (event: SyntheticEvent) => {
      event.preventDefault()
      if (inFlightRef.current) return
      inFlightRef.current = true
      try {
        setSubmitted(true)
        if (!spec || !form.type) return
        const prep = prepareConnectionSubmit(form, spec, mode)
        setErrors(
          prep.retentionError
            ? { ...prep.errors, webhook_receipt_retention_days: prep.retentionError }
            : prep.errors,
        )
        if (!prep.proceed) return
        setSubmitting(true)
        try {
          const shouldClose = await submitConnection({
            form,
            // Narrowed to ConnectionType by the guard above; pass explicitly so
            // the create body needs no `as` cast downstream.
            connectionType: form.type,
            spec,
            mode,
            connection,
            // Fresh capture-binding namespace per submit, generated in the event
            // handler (never during render), so handles can't be replayed.
            draftId: crypto.randomUUID(),
            supportsWebhook: prep.supportsWebhook,
            retentionValue: prep.retentionValue,
            createConnection,
            updateConnection,
            captureSecret,
          })
          if (shouldClose) onClose()
        } finally {
          setSubmitting(false)
        }
      } finally {
        inFlightRef.current = false
      }
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
      setSubmitted,
      setErrors,
    ],
  )
  return { handleSubmit, submitting }
}
