import { useState } from 'react'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import type { VersionHistoryClient } from '@/api/endpoints/version-history'

const log = createLogger('rollback-confirm')

export interface RollbackConfirmDialogProps<T> {
  client: VersionHistoryClient<T>
  toVersion: number | null
  open: boolean
  onClose: () => void
  onSuccess?: () => void
}

function RollbackReasonBody({
  validationError,
  reason,
  onChange,
}: {
  validationError: string | null
  reason: string
  onChange: (value: string) => void
}) {
  return (
    <div className="flex flex-col gap-grid-gap">
      {validationError && (
        <ErrorBanner severity="warning" title={validationError} />
      )}
      <InputField
        label="Reason"
        hint="Required for the audit trail."
        value={reason}
        onChange={(e) => onChange(e.target.value)}
        multiline
        rows={3}
        required
      />
    </div>
  )
}

/**
 * Confirmation dialog for rolling a versioned resource back to an
 * earlier snapshot.  Surfaces a required reason field; the backend
 * audits the rollback under the supplied reason.
 *
 * Generic over the snapshot payload type. The dialog collects only a
 * target version and a reason; the client's per-domain ``rollback``
 * function maps that onto its own wire body and the dialog discards the
 * returned entity (it just toasts success).
 */
export function RollbackConfirmDialog<T>({
  client,
  toVersion,
  open,
  onClose,
  onSuccess,
}: RollbackConfirmDialogProps<T>) {
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [validationError, setValidationError] = useState<string | null>(null)

  // ``version`` arrives from the caller guarded against null, so the
  // function body can rely on it without a non-null assertion.
  const submitRollback = async (version: number): Promise<boolean> => {
    try {
      await client.rollback({ targetVersion: version, reason: reason.trim() })
      useToastStore.getState().add({
        variant: 'success',
        title: `Rolled back to v${version}`,
      })
      return true
    } catch (err) {
      log.warn('rollback_failed', { error: sanitizeForLog(getErrorMessage(err)) })
      useToastStore.getState().add({
        variant: 'error',
        title: 'Rollback failed',
        description: getErrorMessage(err),
      })
      return false
    } finally {
      setSubmitting(false)
    }
  }

  const handleConfirm = async (): Promise<void> => {
    if (submitting || toVersion === null) return
    if (!reason.trim()) {
      setValidationError('Rollback requires a reason for the audit log.')
      return
    }
    setValidationError(null)
    setSubmitting(true)
    const succeeded = await submitRollback(toVersion)
    // Run the success callback OUTSIDE the rollback try/catch so a
    // throw from the host page (e.g. a failed re-fetch) does not
    // surface as "Rollback failed": the rollback already committed
    // server-side.
    if (!succeeded) return
    try {
      onSuccess?.()
    } catch (err) {
      log.warn('rollback_on_success_callback_failed', {
        error: sanitizeForLog(getErrorMessage(err)),
      })
    }
    setReason('')
    onClose()
  }

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          setReason('')
          setValidationError(null)
          onClose()
        }
      }}
      title={toVersion === null ? 'Rollback' : `Rollback to v${toVersion}?`}
      description="The current configuration will be overwritten with the selected snapshot. The rollback is recorded in the audit log."
      variant="destructive"
      confirmLabel={submitting ? 'Rolling back…' : 'Roll back'}
      onConfirm={handleConfirm}
      loading={submitting}
    >
      <RollbackReasonBody
        validationError={validationError}
        reason={reason}
        onChange={setReason}
      />
    </ConfirmDialog>
  )
}
