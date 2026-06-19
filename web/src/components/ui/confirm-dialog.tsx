import { useState } from 'react'
import { AlertDialog } from '@base-ui/react/alert-dialog'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { Button } from './button'

const log = createLogger('ConfirmDialog')

/**
 * Confirm-handler contract. Resolving to ``false`` keeps the dialog open
 * so the caller can retry from the same surface (used by sentinel-
 * returning store mutations). Any other resolution (``void`` / sync or
 * async / ``true``) closes the dialog. The ``void`` arm is intentional so
 * a plain void-returning handler body is accepted; that is exactly what
 * ``no-invalid-void-type`` flags, hence the single suppression here.
 */
// eslint-disable-next-line @typescript-eslint/no-invalid-void-type -- intentional confirm-handler contract: false keeps the dialog open, void (sync or async) closes it
export type ConfirmHandler = () => boolean | void | Promise<boolean | void>

export interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string | undefined
  /** Label for the confirm button (default: "Confirm"). */
  confirmLabel?: string | undefined
  /** Label for the cancel button (default: "Cancel"). */
  cancelLabel?: string | undefined
  /** Visual variant (default: "default"). "destructive" uses a red confirm button. */
  variant?: 'default' | 'destructive' | undefined
  /** Confirm handler; see :type:`ConfirmHandler` for the resolution contract. */
  onConfirm: ConfirmHandler
  /**
   * Optional handler invoked when the user explicitly clicks the
   * Cancel button. Dismissals via Escape or backdrop click do NOT
   * trigger this callback -- they only fire ``onOpenChange(false)``.
   * Use this to distinguish "explicit reject" from "dismiss without
   * action" at the call site.
   */
  onCancel?: (() => void) | undefined
  /** Whether the confirm action is in progress. */
  loading?: boolean | undefined
  className?: string | undefined
  /** Optional content rendered between description and action buttons. */
  children?: React.ReactNode
}

function ConfirmDialogActions({
  busy,
  variant,
  cancelLabel,
  confirmLabel,
  onCancel,
  onConfirmClick,
}: {
  busy: boolean
  variant: 'default' | 'destructive'
  cancelLabel: string
  confirmLabel: string
  onCancel?: (() => void) | undefined
  onConfirmClick: () => void
}) {
  return (
    <div className="mt-6 flex justify-end gap-3">
      <AlertDialog.Close
        render={
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => onCancel?.()}
          >
            {cancelLabel}
          </Button>
        }
      />
      <Button
        variant={variant === 'destructive' ? 'destructive' : 'default'}
        data-variant={variant}
        disabled={busy}
        onClick={onConfirmClick}
      >
        {busy && (
          <Loader2 className="mr-2 size-4 animate-spin" aria-hidden="true" />
        )}
        {confirmLabel}
      </Button>
    </div>
  )
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  variant = 'default',
  onConfirm,
  onCancel,
  loading = false,
  className,
  children,
}: ConfirmDialogProps) {
  const [submitting, setSubmitting] = useState(false)
  const busy = loading || submitting

  const handleConfirmClick = async (): Promise<void> => {
    if (busy) return
    setSubmitting(true)
    try {
      const result = await onConfirm()
      // Stay open when the caller explicitly signals failure via a
      // `false` return (sentinel store mutations); any other return
      // value (void / true / undefined) closes.
      if (result !== false) onOpenChange(false)
    } catch (err) {
      // Dialog stays open on a thrown error too, so the caller can
      // retry from the same surface. Log the cause in case the caller
      // forgets to toast.
      log.warn('ConfirmDialog onConfirm threw', { title: sanitizeForLog(title) }, err)
    } finally {
      setSubmitting(false)
    }
  }

  const handleOpenChange = (nextOpen: boolean): void => {
    // Lock the dialog while a confirm action is in flight: without
    // this, Escape and backdrop clicks flow straight through to
    // ``onOpenChange`` and callers that clear state on close (e.g.
    // ApprovalDetailDrawer resetting its ``comment`` state) would drop
    // the user's retry context mid-operation, even though this
    // component stays open on failure so the caller can retry from
    // the same surface.
    if (busy && !nextOpen) return
    onOpenChange(nextOpen)
  }

  return (
    <AlertDialog.Root open={open} onOpenChange={handleOpenChange}>
      <AlertDialog.Portal>
        <AlertDialog.Backdrop
          className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm transition-opacity duration-200 ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0"
        />
        <AlertDialog.Popup
          className={cn(
            'fixed top-1/2 left-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border-bright bg-surface p-card shadow-[var(--so-shadow-card-hover)]',
            'transition-[opacity,translate,scale] duration-200 ease-out',
            'data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0',
            'data-[closed]:scale-95 data-[starting-style]:scale-95 data-[ending-style]:scale-95',
            className,
          )}
        >
          <AlertDialog.Title className="text-base font-semibold text-foreground">
            {title}
          </AlertDialog.Title>
          {description && (
            <AlertDialog.Description className="mt-2 text-sm text-muted-foreground">
              {description}
            </AlertDialog.Description>
          )}
          {children}
          <ConfirmDialogActions
            busy={busy}
            variant={variant}
            cancelLabel={cancelLabel}
            confirmLabel={confirmLabel}
            onCancel={onCancel}
            onConfirmClick={() => { void handleConfirmClick() }}
          />
        </AlertDialog.Popup>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  )
}
