import { AnimatePresence, motion } from 'motion/react'
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react'
import { toastEntrance } from '@/lib/motion'
import { cn } from '@/lib/utils'
import type { ToastItem, ToastVariant } from '@/stores/toast'
import { useToastStore } from '@/stores/toast'
import { Button } from './button'

const VARIANT_CONFIG: Record<
  ToastVariant,
  { icon: React.ElementType; borderClass: string }
> = {
  success: { icon: CheckCircle2, borderClass: 'border-l-success' },
  error: { icon: XCircle, borderClass: 'border-l-danger' },
  warning: { icon: AlertTriangle, borderClass: 'border-l-warning' },
  info: { icon: Info, borderClass: 'border-l-accent' },
}

export interface ToastProps {
  toast: ToastItem
  onDismiss: (id: string) => void
}

export function Toast({ toast, onDismiss }: ToastProps) {
  const config = VARIANT_CONFIG[toast.variant]
  const Icon = config.icon
  const ariaLive = toast.variant === 'error' ? 'assertive' : 'polite'

  return (
    <motion.div
      layout
      variants={toastEntrance}
      initial="initial"
      animate="animate"
      exit="exit"
      role={toast.variant === 'error' ? 'alert' : 'status'}
      aria-live={ariaLive}
      className={cn(
        'pointer-events-auto flex w-80 items-start gap-3 rounded-lg border border-border-bright bg-surface p-card shadow-lg',
        'border-l-4',
        config.borderClass,
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0 text-foreground" aria-hidden="true" />
      <div className="flex-1 space-y-0.5">
        <p className="text-sm font-medium text-foreground">{toast.title}</p>
        {toast.description && (
          <p className="text-xs text-muted-foreground">{toast.description}</p>
        )}
        {toast.action && (
          <Button
            variant="ghost"
            size="sm"
            className="mt-1 h-6 px-2 text-xs text-accent hover:text-accent"
            onClick={() => {
              toast.action?.onClick()
              onDismiss(toast.id)
            }}
          >
            {toast.action.label}
          </Button>
        )}
      </div>
      {toast.dismissible !== false && (
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => onDismiss(toast.id)}
          aria-label="Dismiss notification"
        >
          <X className="size-3.5" />
        </Button>
      )}
    </motion.div>
  )
}

export interface ToastContainerProps {
  /** Maximum visible toasts (default: 3). */
  maxVisible?: number
}

export function ToastContainer({ maxVisible = 3 }: ToastContainerProps) {
  const toasts = useToastStore((s) => s.toasts)
  const dismiss = useToastStore((s) => s.dismiss)
  const visible = toasts.slice(-maxVisible)

  return (
    <div
      // z-[60] keeps toasts above modal backdrop / popup / confirm-dialog
      // (all z-50) so a save-failure toast is never hidden behind an open
      // dialog.
      className="pointer-events-none fixed bottom-6 right-6 z-[60] flex flex-col-reverse gap-2"
      aria-label="Notifications"
    >
      <AnimatePresence mode="popLayout">
        {visible.map((toast) => (
          <Toast key={toast.id} toast={toast} onDismiss={dismiss} />
        ))}
      </AnimatePresence>
    </div>
  )
}
