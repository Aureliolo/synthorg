import { useState } from 'react'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { InputField } from '@/components/ui/input-field'

interface TriggerMeetingDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /**
   * Trigger handler. Resolving to ``false`` keeps the dialog open
   * (validation failure or a failed trigger); any other resolution
   * closes it -- matches the ``ConfirmDialog.onConfirm`` sentinel
   * contract.
   */
  onConfirm: (eventName: string) => Promise<boolean | undefined>
  loading?: boolean
}

export function TriggerMeetingDialog({
  open,
  onOpenChange,
  onConfirm,
  loading,
}: TriggerMeetingDialogProps) {
  const [eventName, setEventName] = useState('')
  const [validationError, setValidationError] = useState<string | null>(null)

  const handleConfirm = async (): Promise<boolean | undefined> => {
    const trimmed = eventName.trim()
    if (!trimmed) {
      setValidationError('Event name is required.')
      return false
    }
    setValidationError(null)
    const result = await onConfirm(trimmed)
    if (result === false) return false
    setEventName('')
  }

  const handleOpenChange = (o: boolean) => {
    onOpenChange(o)
    if (!o) {
      setEventName('')
      setValidationError(null)
    }
  }

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={handleOpenChange}
      title="Trigger Meeting"
      description="Enter the event name to trigger an event-based meeting."
      confirmLabel="Trigger"
      onConfirm={handleConfirm}
      loading={loading}
    >
      <div className="mt-3">
        <InputField
          label="Event Name"
          value={eventName}
          onChange={(e) => { setEventName(e.target.value); setValidationError(null) }}
          placeholder="e.g. on_pr, deploy_complete"
          error={validationError}
          required
        />
      </div>
    </ConfirmDialog>
  )
}
