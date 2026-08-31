import { useState } from 'react'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { InputField } from '@/components/ui/input-field'
import { useProvidersStore } from '@/stores/providers'

/**
 * Collects a confirm + reason for a capability-override write the backend
 * rejected with ``SECURITY_TOGGLE_CONFIRM_REQUIRED``: forcing
 * ``supports_vision`` onto the model bound to ``security.vision_verify_model``.
 * The write is staged in the providers store's ``pendingCapabilityOverridesConfirm``;
 * confirming retries it with ``confirm: true`` and the reason (recorded in the
 * security audit trail), dismissing discards it.
 */
export function CapabilityOverridesConfirmDialog(): React.ReactNode {
  const pending = useProvidersStore((s) => s.pendingCapabilityOverridesConfirm)
  const confirmPendingCapabilityOverrides = useProvidersStore(
    (s) => s.confirmPendingCapabilityOverrides,
  )
  const dismissPendingCapabilityOverridesConfirm = useProvidersStore(
    (s) => s.dismissPendingCapabilityOverridesConfirm,
  )
  const [reason, setReason] = useState('')

  const close = (): void => {
    setReason('')
    dismissPendingCapabilityOverridesConfirm()
  }

  return (
    <ConfirmDialog
      open={pending !== null}
      onOpenChange={(open) => {
        if (!open) close()
      }}
      title="Confirm a vision-capability override?"
      description={
        pending
          ? `Forcing vision support on "${pending.modelId}" changes the model backing the`
            + ` vision-verify gate. It needs a deliberate confirmation and a reason for the`
            + ` audit trail.`
          : ''
      }
      confirmLabel="Confirm override"
      variant="destructive"
      onConfirm={async () => {
        const result = await confirmPendingCapabilityOverrides(reason)
        setReason('')
        return result
      }}
      onCancel={close}
    >
      <InputField
        multiline
        label="Reason"
        hint="Recorded in the security audit trail. A descriptive default is used if left blank."
        placeholder="Why is this model's vision support being declared?"
        rows={2}
        value={reason}
        onValueChange={setReason}
      />
    </ConfirmDialog>
  )
}
