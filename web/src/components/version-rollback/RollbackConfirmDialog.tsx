import { useState } from 'react'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import type { VersionHistoryClient } from '@/api/endpoints/version-history'

const log = createLogger('rollback-confirm')

export interface RollbackConfirmDialogProps<T> {
  client: VersionHistoryClient<T>
  toVersion: number | null
  open: boolean
  onClose: () => void
  onSuccess?: () => void
}

/**
 * Confirmation dialog for rolling a versioned resource back to an
 * earlier snapshot.  Surfaces a required reason field; the backend
 * audits the rollback under the supplied reason.
 *
 * Generic over the snapshot payload type; the only constraint is
 * that ``client.rollback`` resolves to a ``VersionSnapshot``.
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

  const handleConfirm = async (): Promise<void> => {
    if (submitting) return
    if (toVersion === null) return
    if (!reason.trim()) {
      setValidationError('Rollback requires a reason for the audit log.')
      return
    }
    setValidationError(null)
    setSubmitting(true)
    let succeeded = false
    try {
      await client.rollback({ to_version: toVersion, reason: reason.trim() })
      succeeded = true
      useToastStore.getState().add({
        variant: 'success',
        title: `Rolled back to v${toVersion}`,
      })
    } catch (err) {
      log.warn('Rollback failed:', getErrorMessage(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Rollback failed',
        description: getErrorMessage(err),
      })
    } finally {
      setSubmitting(false)
    }
    // Run the success callback OUTSIDE the rollback try/catch so a
    // throw from the host page (e.g. a failed re-fetch) does not
    // surface as "Rollback failed": the rollback already committed
    // server-side.
    if (succeeded) {
      try {
        onSuccess?.()
      } catch (err) {
        log.warn('Rollback onSuccess callback failed:', getErrorMessage(err))
      }
      setReason('')
      onClose()
    }
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
      <div className="flex flex-col gap-grid-gap">
        {validationError && (
          <ErrorBanner severity="warning" title={validationError} />
        )}
        <InputField
          label="Reason"
          hint="Required for the audit trail."
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          multiline
          rows={3}
          required
        />
      </div>
    </ConfirmDialog>
  )
}
