import { useMemo, useRef, useState } from 'react'
import { ArrowLeft } from 'lucide-react'
import {
  CONNECTION_TYPE_VALUES,
  connectionTypeUsesWebhookReceipts,
  type Connection,
  type ConnectionType,
  type CreateConnectionRequest,
} from '@/api/types/integrations'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogCloseButton,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import { validateA2APeerCredentials } from './connection-type-fields'
import { cn } from '@/lib/utils'
import { useConnectionsStore } from '@/stores/connections'
import {
  CONNECTION_TYPE_FIELDS,
  type ConnectionFieldSpec,
  validateConnectionField,
  validateConnectionName,
} from './connection-type-fields'
import { TypeBadge } from './TypeBadge'

type Mode = 'create' | 'edit'

export interface ConnectionFormModalProps {
  open: boolean
  mode: Mode
  initialType?: ConnectionType
  connection?: Connection | null
  onClose: () => void
}

interface FormState {
  name: string
  type: ConnectionType | null
  topLevel: Record<string, string>
  credentials: Record<string, string>
  /**
   * Webhook receipt retention input as a string so the empty state
   * (= use the global default) round-trips cleanly through `<input>`.
   * Parsed to `number | null` on submit.
   */
  webhookRetention: string
}

const EMPTY_STATE: FormState = {
  name: '',
  type: null,
  topLevel: {},
  credentials: {},
  webhookRetention: '',
}

function makeInitialState(
  mode: Mode,
  initialType: ConnectionType | undefined,
  connection: Connection | null | undefined,
): FormState {
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
    }
  }
  return {
    ...EMPTY_STATE,
    type: initialType ?? null,
  }
}

/**
 * Parse the webhook-retention input into the wire shape.
 *
 * `''` -> `null` (clear override / use global default).
 * `'<int>'` -> the integer.
 * Anything else -> validation error.
 */
function parseRetentionDays(
  raw: string,
): { ok: true; value: number | null } | { ok: false; error: string } {
  const trimmed = raw.trim()
  if (trimmed === '') return { ok: true, value: null }
  if (!/^\d+$/.test(trimmed)) {
    return { ok: false, error: 'Must be a non-negative integer or blank' }
  }
  const parsed = Number.parseInt(trimmed, 10)
  if (!Number.isFinite(parsed) || parsed < 0) {
    return { ok: false, error: 'Must be a non-negative integer or blank' }
  }
  return { ok: true, value: parsed }
}

function renderField(
  spec: ConnectionFieldSpec,
  value: string,
  error: string | null,
  onChange: (value: string) => void,
  readOnly: boolean,
) {
  if (spec.type === 'select' && spec.options) {
    return (
      <SelectField
        key={spec.key}
        label={spec.label}
        value={value}
        options={spec.options.map((o) => ({ value: o, label: o }))}
        hint={spec.hint}
        error={error ?? undefined}
        required={spec.required}
        disabled={readOnly}
        onChange={onChange}
      />
    )
  }
  return (
    <InputField
      key={spec.key}
      label={spec.label}
      type={spec.type === 'select' ? 'text' : spec.type}
      value={value}
      placeholder={spec.placeholder}
      hint={spec.hint}
      error={error}
      required={spec.required}
      disabled={readOnly}
      onValueChange={onChange}
    />
  )
}

export function ConnectionFormModal({
  open,
  mode,
  initialType,
  connection,
  onClose,
}: ConnectionFormModalProps) {
  const mutating = useConnectionsStore((s) => s.mutating)
  const createConnection = useConnectionsStore((s) => s.createConnection)
  const updateConnection = useConnectionsStore((s) => s.updateConnection)

  const [form, setForm] = useState<FormState>(() =>
    makeInitialState(mode, initialType, connection),
  )
  const [errors, setErrors] = useState<Record<string, string | null>>({})
  const [submitted, setSubmitted] = useState(false)

  // Render-phase state sync: reset the form when the dialog transitions
  // from closed -> open, or when the caller hands us a different
  // connection/initial type while already open.
  const prevOpenRef = useRef(open)
  const prevConnectionRef = useRef(connection ?? null)
  const prevInitialTypeRef = useRef(initialType ?? null)
  const prevModeRef = useRef(mode)
  if (
    open &&
    (prevOpenRef.current !== open ||
      prevConnectionRef.current !== (connection ?? null) ||
      prevInitialTypeRef.current !== (initialType ?? null) ||
      prevModeRef.current !== mode)
  ) {
    setForm(makeInitialState(mode, initialType, connection))
    setErrors({})
    setSubmitted(false)
  }
  prevOpenRef.current = open
  prevConnectionRef.current = connection ?? null
  prevInitialTypeRef.current = initialType ?? null
  prevModeRef.current = mode

  const spec = useMemo(
    () => (form.type ? CONNECTION_TYPE_FIELDS[form.type] : null),
    [form.type],
  )

  function handleFieldChange(
    group: 'topLevel' | 'credentials',
    key: string,
    value: string,
  ) {
    setForm((prev) => ({ ...prev, [group]: { ...prev[group], [key]: value } }))
    if (errors[key]) setErrors((prev) => ({ ...prev, [key]: null }))
  }

  function validateAll(): boolean {
    if (!spec) return false
    const dialect =
      form.type === 'database'
        ? form.credentials.dialect ?? ''
        : undefined
    const nextErrors: Record<string, string | null> = {}
    if (mode === 'create') {
      nextErrors.name = validateConnectionName(form.name)
    }
    for (const field of spec.topLevelFields) {
      nextErrors[field.key] = validateConnectionField(
        field,
        form.topLevel[field.key] ?? '',
        dialect,
      )
    }
    if (mode === 'create') {
      for (const field of spec.credentialFields) {
        nextErrors[field.key] = validateConnectionField(
          field,
          form.credentials[field.key] ?? '',
          dialect,
        )
      }
    }
    // A2A peer: scheme-aware credential validation.
    if (form.type === 'a2a_peer' && mode === 'create') {
      const scheme = form.credentials.auth_scheme ?? 'api_key'
      const schemeErrors = validateA2APeerCredentials(scheme, form.credentials)
      for (const [key, msg] of Object.entries(schemeErrors)) {
        const errorKey = key === '_scheme' ? 'auth_scheme' : key
        if (!nextErrors[errorKey]) nextErrors[errorKey] = msg
      }
    }
    setErrors(nextErrors)
    return Object.values(nextErrors).every((v) => v === null)
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setSubmitted(true)
    if (!validateAll() || !form.type || !spec) return

    const retention = parseRetentionDays(form.webhookRetention)
    if (!retention.ok) {
      setErrors((prev) => ({
        ...prev,
        webhook_receipt_retention_days: retention.error,
      }))
      return
    }

    if (mode === 'create') {
      const credentials: Record<string, string> = {}
      for (const field of spec.credentialFields) {
        const value = form.credentials[field.key]
        if (value !== undefined && value !== '') credentials[field.key] = value
      }
      const body: CreateConnectionRequest = {
        name: form.name.trim(),
        connection_type: form.type,
        auth_method: spec.defaultAuthMethod,
        credentials,
        base_url: form.topLevel.base_url?.trim() || null,
        webhook_receipt_retention_days: retention.value,
      }
      const result = await createConnection(body)
      if (result) onClose()
    } else if (connection) {
      const result = await updateConnection(connection.name, {
        base_url: form.topLevel.base_url?.trim() || null,
        webhook_receipt_retention_days: retention.value,
      })
      if (result) onClose()
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <div className="flex items-center gap-2">
            {mode === 'create' && form.type !== null && (
              <Button
                type="button"
                size="icon"
                variant="ghost"
                aria-label="Back to type picker"
                onClick={() =>
                  setForm((prev) => ({
                    ...prev,
                    type: null,
                    topLevel: {},
                    credentials: {},
                  }))
                }
              >
                <ArrowLeft className="size-4" aria-hidden />
              </Button>
            )}
            <DialogTitle>
              {mode === 'create' ? 'New connection' : `Edit ${connection?.name ?? ''}`}
            </DialogTitle>
          </div>
          <DialogCloseButton />
        </DialogHeader>

        {/* Responsive max-h: phones get 85dvh (more usable height
            when the on-screen keyboard is up); desktop sits at 70dvh
            so the modal stays centred with surrounding context. ``dvh``
            (dynamic viewport height) shrinks correctly when the
            mobile browser surfaces its address bar / keyboard, where
            ``vh`` would lock to the static layout viewport and
            overflow off-screen. */}
        <div className="max-h-[85dvh] overflow-y-auto p-card sm:max-h-[70dvh]">
          {mode === 'create' && form.type === null ? (
            <TypePicker
              onSelect={(type) => setForm((prev) => ({ ...prev, type }))}
            />
          ) : (
            spec && (
              <form onSubmit={handleSubmit} className="flex flex-col gap-4">
                <div className="flex items-center gap-2 text-sm text-text-secondary">
                  <TypeBadge type={form.type as ConnectionType} />
                  <span>{spec.description}</span>
                </div>

                {mode === 'create' && (
                  <InputField
                    label="Connection name"
                    placeholder="e.g. primary-github"
                    value={form.name}
                    onValueChange={(v) => {
                      setForm((prev) => ({ ...prev, name: v }))
                      if (errors.name) setErrors((prev) => ({ ...prev, name: null }))
                    }}
                    error={submitted ? errors.name : null}
                    required
                  />
                )}

                {spec.topLevelFields.map((field) =>
                  renderField(
                    field,
                    form.topLevel[field.key] ?? '',
                    submitted ? errors[field.key] ?? null : null,
                    (value) => handleFieldChange('topLevel', field.key, value),
                    false,
                  ),
                )}

                {mode === 'create' &&
                  spec.credentialFields.map((field) =>
                    renderField(
                      field,
                      form.credentials[field.key] ?? '',
                      submitted ? errors[field.key] ?? null : null,
                      (value) =>
                        handleFieldChange('credentials', field.key, value),
                      false,
                    ),
                  )}

                {mode === 'edit' && (
                  <p className="rounded-md bg-surface p-card text-xs text-text-muted">
                    Credentials can only be set at creation time. Delete and
                    recreate the connection to rotate secrets.
                  </p>
                )}

                {form.type !== null
                  && connectionTypeUsesWebhookReceipts(form.type) && (
                  <InputField
                    label="Webhook receipt retention (days)"
                    type="number"
                    value={form.webhookRetention}
                    placeholder="Use system default"
                    hint="Leave blank to use the system default. Set to 0 to never delete this connection's webhook receipts."
                    error={submitted ? errors.webhook_receipt_retention_days : null}
                    onValueChange={(v) => {
                      setForm((prev) => ({ ...prev, webhookRetention: v }))
                      if (errors.webhook_receipt_retention_days) {
                        setErrors((prev) => ({
                          ...prev,
                          webhook_receipt_retention_days: null,
                        }))
                      }
                    }}
                  />
                )}

                <div className="mt-2 flex justify-end gap-2">
                  <Button type="button" variant="ghost" onClick={onClose}>
                    Cancel
                  </Button>
                  <Button type="submit" disabled={mutating}>
                    {mutating
                      ? 'Saving...'
                      : mode === 'create'
                        ? 'Create connection'
                        : 'Save changes'}
                  </Button>
                </div>
              </form>
            )
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

function TypePicker({
  onSelect,
}: {
  onSelect: (type: ConnectionType) => void
}) {
  return (
    <div className="grid grid-cols-2 gap-grid-gap max-[767px]:grid-cols-1">
      {CONNECTION_TYPE_VALUES.map((type) => {
        const spec = CONNECTION_TYPE_FIELDS[type]
        return (
          <button
            key={type}
            type="button"
            onClick={() => onSelect(type)}
            className={cn(
              'flex flex-col gap-1 rounded-lg border border-border bg-card p-card text-left',
              'transition-all duration-200',
              'hover:bg-card-hover hover:-translate-y-px hover:shadow-[var(--so-shadow-card-hover)]',
              'focus:outline-none focus:ring-2 focus:ring-accent',
            )}
          >
            <span className="text-sm font-medium text-foreground">
              {spec.label}
            </span>
            <span className="text-xs text-text-secondary">
              {spec.description}
            </span>
          </button>
        )
      })}
    </div>
  )
}
