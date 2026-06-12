import { ArrowLeft } from 'lucide-react'
import {
  CONNECTION_TYPE_VALUES,
  connectionTypeUsesWebhookReceipts,
  type Connection,
  type ConnectionType,
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
import { ToggleField } from '@/components/ui/toggle-field'
import { cn } from '@/lib/utils'
import { CONNECTION_TYPE_FIELDS, type ConnectionFieldSpec } from './connection-type-fields'
import { TypeBadge } from './TypeBadge'
import { type ConnectionForm, type Mode, useConnectionForm } from './useConnectionForm'

export interface ConnectionFormModalProps {
  open: boolean
  mode: Mode
  initialType?: ConnectionType
  connection?: Connection | null
  onClose: () => void
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

function TypePicker({ onSelect }: { onSelect: (type: ConnectionType) => void }) {
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
            <span className="text-sm font-medium text-foreground">{spec.label}</span>
            <span className="text-xs text-text-secondary">{spec.description}</span>
          </button>
        )
      })}
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
  errors: Record<string, string | null>
  submitted: boolean
  onChange: (key: string, value: string) => void
}

function ConnectionFieldList({ fields, values, errors, submitted, onChange }: FieldListProps) {
  return (
    <>
      {fields.map((field) =>
        renderField(
          field,
          values[field.key] ?? '',
          submitted ? (errors[field.key] ?? null) : null,
          (value) => onChange(field.key, value),
          false,
        ),
      )}
    </>
  )
}

function WebhookRetentionField({ f }: { f: ConnectionForm }) {
  if (f.form.type === null || !connectionTypeUsesWebhookReceipts(f.form.type)) return null
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

function ConnectionFormFields({ f, mode, onClose }: { f: ConnectionForm; mode: Mode; onClose: () => void }) {
  const spec = f.spec!
  return (
    <form onSubmit={f.handleSubmit} className="flex flex-col gap-4">
      <div className="flex items-center gap-2 text-sm text-text-secondary">
        <TypeBadge type={f.form.type as ConnectionType} />
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

      <ConnectionFieldList
        fields={spec.topLevelFields}
        values={f.form.topLevel}
        errors={f.errors}
        submitted={f.submitted}
        onChange={(key, value) => f.handleFieldChange('topLevel', key, value)}
      />

      {mode === 'create' && (
        <ConnectionFieldList
          fields={spec.credentialFields}
          values={f.form.credentials}
          errors={f.errors}
          submitted={f.submitted}
          onChange={(key, value) => f.handleFieldChange('credentials', key, value)}
        />
      )}

      {mode === 'edit' && (
        <p className="rounded-md bg-surface p-card text-xs text-text-muted">
          Credentials can only be set at creation time. Delete and recreate the connection to rotate secrets.
        </p>
      )}

      <WebhookRetentionField f={f} />

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
            Boolean(f.spec) && <ConnectionFormFields f={f} mode={mode} onClose={onClose} />
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
