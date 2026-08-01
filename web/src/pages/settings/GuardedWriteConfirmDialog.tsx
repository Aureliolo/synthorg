import { useState } from 'react'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { InputField } from '@/components/ui/input-field'
import { useSettingsStore } from '@/stores/settings'

/**
 * Collects a confirm + reason for a guarded settings write the backend rejected
 * with ``SECURITY_TOGGLE_CONFIRM_REQUIRED``. The write is staged in the settings
 * store's ``pendingConfirm``; confirming retries it with ``confirm: true`` and
 * the reason (recorded in the security audit trail), dismissing discards it.
 */
export function GuardedWriteConfirmDialog(): React.ReactNode {
  const pending = useSettingsStore((s) => s.pendingConfirm)
  const confirmPendingUpdate = useSettingsStore((s) => s.confirmPendingUpdate)
  const dismissPendingConfirm = useSettingsStore((s) => s.dismissPendingConfirm)
  const [reason, setReason] = useState('')

  const close = (): void => {
    setReason('')
    dismissPendingConfirm()
  }

  return (
    <ConfirmDialog
      open={pending !== null}
      onOpenChange={(open) => {
        if (!open) close()
      }}
      title="Confirm a guardrail-weakening change?"
      description={
        pending
          ? `Changing ${pending.ns}/${pending.key} relaxes a security or output`
            + ` guardrail. It needs a deliberate confirmation and a reason for the`
            + ` audit trail.`
          : ''
      }
      confirmLabel="Confirm change"
      variant="destructive"
      onConfirm={async () => {
        const result = await confirmPendingUpdate(reason)
        setReason('')
        return result !== null
      }}
      onCancel={close}
    >
      <InputField
        multiline
        label="Reason"
        hint="Recorded in the security audit trail. A descriptive default is used if left blank."
        placeholder="Why is this change being made?"
        rows={2}
        value={reason}
        onValueChange={setReason}
      />
    </ConfirmDialog>
  )
}
