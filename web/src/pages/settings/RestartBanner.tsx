import { AnimatePresence, motion } from 'motion/react'
import { springDefault } from '@/lib/motion'
import { AlertTriangle, RotateCw, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useCapabilities } from '@/hooks/useCapabilities'
import { useRestartStore } from '@/stores/restart'

export interface RestartBannerProps {
  count: number
  onDismiss: () => void
}

// Shown instead of the control where the process is not supervised. Offering
// a button whose only possible outcome is a refusal reads as a broken button;
// saying why up front is the same information without the dead end.
const UNSUPERVISED_NOTE =
  'This process is not supervised, so it cannot restart itself: restart it the ' +
  'way this deployment is started.'

/**
 * Notice that saved settings are not in effect yet, and the control that puts
 * them in effect.
 *
 * The restart lives here rather than in a separate admin corner because this
 * is where the operator learns they need one. Without it the only way to apply
 * a documented, dashboard-editable setting is to leave the product and find a
 * shell, which makes the setting effectively unreachable through the interface
 * that offered it.
 */
export function RestartBanner({ count, onDismiss }: RestartBannerProps) {
  const restarting = useRestartStore((s) => s.restarting)
  const restart = useRestartStore((s) => s.restart)
  const { capabilities } = useCapabilities()
  const message = count === 1
    ? '1 setting requires a restart to take effect.'
    : `${count} settings require a restart to take effect.`

  return (
    <AnimatePresence>
      {count > 0 && (
        <motion.div
          key="restart-banner"
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={springDefault}
          className="flex items-center gap-3 rounded-lg border border-warning/30 bg-warning/5 p-card"
          role="alert"
        >
          <AlertTriangle className="size-4 shrink-0 text-warning" aria-hidden />
          <span className="flex-1 text-sm text-warning">
            {restarting ? 'Restarting the backend...' : message}
            {!capabilities.restart && ` ${UNSUPERVISED_NOTE}`}
          </span>
          {capabilities.restart && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void restart()}
              disabled={restarting}
            >
              <RotateCw
                className={restarting ? 'size-3.5 animate-spin' : 'size-3.5'}
                aria-hidden
              />
              {restarting ? 'Restarting' : 'Restart now'}
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={onDismiss}
            aria-label="Dismiss"
            disabled={restarting}
            className="text-warning hover:text-warning"
          >
            <X className="size-3.5" aria-hidden />
          </Button>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
