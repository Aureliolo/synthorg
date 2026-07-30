import { ArrowLeft } from 'lucide-react'
import { useMemo } from 'react'
import type { Connection, ConnectionType } from '@/api/types/integrations'
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
import { ToggleField } from '@/components/ui/toggle-field'
import { cn } from '@/lib/utils'
import { useConnectionsStore } from '@/stores/connections'
import {
  type ConnectionFieldSpec,
  type ResolvedConnectionSpec,
  isFieldRequired,
  isFieldVisible,
  metadataGovernsOtherFields,
} from './connection-fields'
import {
  allFormValues,
  isForgeConnectionType,
  webhookRetentionApplies,
} from './connection-submit'
import { RepoScopePicker } from './RepoScopePicker'
import { TypeBadge } from './TypeBadge'
import { type ConnectionForm, type Mode, useConnectionForm } from './useConnectionForm'
import { useConnectionTypes } from './useConnectionTypes'

export interface ConnectionFormModalProps {
  open: boolean
  mode: Mode
  initialType?: ConnectionType | undefined
  connection?: Connection | null | undefined
  onClose: () => void
}

interface RenderFieldArgs {
  spec: ConnectionFieldSpec
  value: string
  error: string | null
  /** Effective requiredness, which for a conditional field depends on the
      rest of the form rather than on the field's own flag. */
  required: boolean
  onChange: (value: string) => void
  readOnly: boolean
}

function renderField({ spec, value, error, required, onChange, readOnly }: RenderFieldArgs) {
  if (spec.type === 'select' && spec.options) {
    return (
      <SelectField
        key={spec.key}
        label={spec.label}
        value={value}
        options={spec.options.map((o) => ({ value: o, label: o }))}
        // Named after the field, so an unanswered select reads as a prompt
        // rather than as an option the operator might think they picked.
        placeholder={`Select a ${spec.label.toLowerCase()}`}
        hint={spec.hint}
        error={error ?? undefined}
        required={required}
        disabled={readOnly}
        onChange={onChange}
      />
    )
  }
  // A secret field is captured out of band on submit (only an opaque handle
  // reaches the create call), so surface that rather than leaving it implicit.
  const secretHint = 'Captured securely; never stored in the connection payload.'
  const hint = spec.secret ? (spec.hint ? `${spec.hint} ${secretHint}` : secretHint) : spec.hint
  return (
    <InputField
      key={spec.key}
      label={spec.label}
      type={spec.type === 'select' ? 'text' : spec.type}
      value={value}
      placeholder={spec.placeholder}
      hint={hint}
      error={error}
      required={required}
      disabled={readOnly}
      onValueChange={onChange}
    />
  )
}

function ConnectionTypeCard({
  label,
  description,
  onSelect,
}: {
  label: string
  description: string
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        'flex flex-col gap-1 rounded-lg border border-border bg-card p-card text-left',
        'transition-all duration-200',
        'hover:bg-card-hover hover:-translate-y-px hover:shadow-[var(--so-shadow-card-hover)]',
        'focus:outline-none focus:ring-2 focus:ring-accent',
      )}
    >
      <span className="text-sm font-medium text-foreground">{label}</span>
      <span className="text-xs text-text-secondary">{description}</span>
    </button>
  )
}

function TypePicker({ onSelect }: { onSelect: (type: ConnectionType) => void }) {
  // The registry is the single source of truth for which types exist and how
  // they are labelled/described; render exactly what the backend returns.
  const connectionTypes = useConnectionTypes()
  const typesLoading = useConnectionsStore((s) => s.typesLoading)
  const typesError = useConnectionsStore((s) => s.typesError)
  const fetchConnectionTypes = useConnectionsStore((s) => s.fetchConnectionTypes)
  if (connectionTypes.length === 0) {
    if (typesLoading) {
      return (
        <p className="p-card text-sm text-text-secondary">
          Loading connection types...
        </p>
      )
    }
    // Distinguish a failed fetch from a genuinely empty registry so the
    // operator gets a retry instead of a misleading "none available".
    if (typesError !== null) {
      return (
        <div className="flex flex-col items-start gap-2 p-card text-sm">
          <p className="text-danger">Could not load connection types.</p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void fetchConnectionTypes()}
          >
            Retry
          </Button>
        </div>
      )
    }
    return (
      <p className="p-card text-sm text-text-secondary">
        No connection types available.
      </p>
    )
  }
  return (
    <div className="grid grid-cols-2 gap-grid-gap max-[767px]:grid-cols-1">
      {connectionTypes.map((meta) => (
        <ConnectionTypeCard
          key={meta.connection_type}
          label={meta.label}
          description={meta.description}
          onSelect={() => onSelect(meta.connection_type)}
        />
      ))}
    </div>
  )
}

function ConnectionFormHeader({
  mode,
  type,
  connectionName,
  onBack,
}: {
  mode: Mode
  type: ConnectionType | null
  connectionName: string | undefined
  onBack: () => void
}) {
  return (
    <DialogHeader>
      <div className="flex items-center gap-2">
        {mode === 'create' && type !== null && (
          <Button type="button" size="icon" variant="ghost" aria-label="Back to type picker" onClick={onBack}>
            <ArrowLeft className="size-4" aria-hidden />
          </Button>
        )}
        <DialogTitle>{mode === 'create' ? 'New connection' : `Edit ${connectionName ?? ''}`}</DialogTitle>
      </div>
      <DialogCloseButton />
    </DialogHeader>
  )
}

interface FieldListProps {
  fields: readonly ConnectionFieldSpec[]
  values: Record<string, string>
  /** Every field's value, whatever its placement, for condition lookups. */
  allValues: Record<string, string>
  errors: Record<string, string | null>
  submitted: boolean
  onChange: (key: string, value: string) => void
}

function ConnectionFieldList({
  fields,
  values,
  allValues,
  errors,
  submitted,
  onChange,
}: FieldListProps) {
  return (
    <>
      {fields
        .filter((field) => isFieldVisible(field, allValues))
        .map((field) =>
          renderField({
            spec: field,
            value: values[field.key] ?? '',
            error: submitted ? (errors[field.key] ?? null) : null,
            required: isFieldRequired(field, allValues),
            onChange: (value) => onChange(field.key, value),
            readOnly: false,
          }),
        )}
    </>
  )
}

function WebhookRetentionField({ f, spec }: { f: ConnectionForm; spec: ResolvedConnectionSpec }) {
  const connectionTypes = useConnectionTypes()
  // The same predicate the submit path gates on, so the control and the
  // submitted body cannot disagree about whether retention applies.
  const applies = useMemo(
    () => webhookRetentionApplies(f.form, spec, connectionTypes),
    [f.form, spec, connectionTypes],
  )
  if (!applies) return null
  return (
    <InputField
      label="Webhook receipt retention (days)"
      type="number"
      value={f.form.webhookRetention}
      placeholder="Use system default"
      hint="Leave blank to use the system default. Set to 0 to never delete this connection's webhook receipts."
      error={f.submitted ? f.errors['webhook_receipt_retention_days'] : null}
      onValueChange={f.setWebhookRetention}
    />
  )
}

function RepoScopeField({ f, mode }: { f: ConnectionForm; mode: Mode }) {
  if (f.form.type === null || !isForgeConnectionType(f.form.type)) return null
  // The scan is a live token-authenticated read against the forge, so it only
  // works once the connection (and its captured credential) exists. On create
  // the connection is fail-closed (deny-all) until the operator scopes it here
  // after saving.
  if (mode === 'create') {
    return (
      <p className="rounded-md bg-surface p-card text-xs text-text-muted">
        This connection starts with no repository access. After creating it, edit the connection
        to scan the token and grant per-repository scope.
      </p>
    )
  }
  return (
    <RepoScopePicker
      connectionName={f.form.name}
      selected={f.form.allowedRepos}
      onChange={f.setAllowedRepos}
    />
  )
}

function ConnectionFieldSections({
  f,
  spec,
  mode,
}: {
  f: ConnectionForm
  spec: ResolvedConnectionSpec
  mode: Mode
}) {
  const allValues = useMemo(() => allFormValues(f.form), [f.form])
  // A metadata field that governs the fields below it has to be answered
  // first, and putting it there keeps the resulting content shift below the
  // control the operator is using. Where no such dependency exists, leading
  // with metadata would only push the credential further down the tab order.
  const metadataLeads = metadataGovernsOtherFields(spec)
  const metadataList = (
    <ConnectionFieldList
      fields={spec.metadataFields}
      values={f.form.metadata}
      allValues={allValues}
      errors={f.errors}
      submitted={f.submitted}
      onChange={(key, value) => f.handleFieldChange('metadata', key, value)}
    />
  )
  return (
    <>
      {metadataLeads && metadataList}

      <ConnectionFieldList
        fields={spec.topLevelFields}
        values={f.form.topLevel}
        allValues={allValues}
        errors={f.errors}
        submitted={f.submitted}
        onChange={(key, value) => f.handleFieldChange('topLevel', key, value)}
      />

      {mode === 'create' && (
        <ConnectionFieldList
          fields={spec.credentialFields}
          values={f.form.credentials}
          allValues={allValues}
          errors={f.errors}
          submitted={f.submitted}
          onChange={(key, value) => f.handleFieldChange('credentials', key, value)}
        />
      )}

      {!metadataLeads && metadataList}
    </>
  )
}

function ConnectionFormFields({
  f,
  spec,
  connectionType,
  mode,
  onClose,
}: {
  f: ConnectionForm
  spec: ResolvedConnectionSpec
  connectionType: ConnectionType
  mode: Mode
  onClose: () => void
}) {
  return (
    <form onSubmit={f.handleSubmit} className="flex flex-col gap-section-gap">
      <div className="flex items-center gap-2 text-sm text-text-secondary">
        <TypeBadge type={connectionType} />
        <span>{spec.description}</span>
      </div>

      {mode === 'create' && (
        <InputField
          label="Connection name"
          placeholder="e.g. primary-github"
          value={f.form.name}
          onValueChange={f.setName}
          error={f.submitted ? f.errors['name'] : null}
          required
        />
      )}

      <ConnectionFieldSections f={f} spec={spec} mode={mode} />

      {mode === 'edit' && (
        <p className="rounded-md bg-surface p-card text-xs text-text-muted">
          Credentials can only be set at creation time. Delete and recreate the connection to rotate secrets.
        </p>
      )}

      <WebhookRetentionField f={f} spec={spec} />

      <RepoScopeField f={f} mode={mode} />

      <ToggleField
        label="Sensitive connection"
        description="Route every external-access call against this connection (read or write) to human approval."
        checked={f.form.sensitive}
        onChange={f.setSensitive}
      />

      <div className="mt-2 flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" disabled={f.mutating}>
          {f.mutating ? 'Saving...' : mode === 'create' ? 'Create connection' : 'Save changes'}
        </Button>
      </div>
    </form>
  )
}

export function ConnectionFormModal(props: ConnectionFormModalProps) {
  const { open, mode, connection, onClose } = props
  const f = useConnectionForm(props)

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <ConnectionFormHeader
          mode={mode}
          type={f.form.type}
          connectionName={connection?.name}
          onBack={f.clearType}
        />

        {/* Responsive max-h: phones get 85dvh (usable height when the
            keyboard is up); desktop sits at 70dvh. `dvh` shrinks with
            the mobile address bar / keyboard where `vh` would overflow. */}
        <div className="max-h-[85dvh] overflow-y-auto p-card sm:max-h-[70dvh]">
          {mode === 'create' && f.form.type === null ? (
            <TypePicker onSelect={f.setType} />
          ) : (
            f.spec !== null &&
            f.form.type !== null && (
              <ConnectionFormFields
                f={f}
                spec={f.spec}
                connectionType={f.form.type}
                mode={mode}
                onClose={onClose}
              />
            )
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
