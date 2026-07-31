import { AnimatePresence, motion } from 'motion/react'
import { springDefault } from '@/lib/motion'
import { AlertTriangle, RotateCw } from 'lucide-react'
import { useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { useRestartStore } from '@/stores/restart'

// Shown instead of the control where the process is not supervised. Offering
// a button whose only possible outcome is a refusal reads as a broken button;
// saying why up front is the same information without the dead end.
const UNSUPERVISED_NOTE =
  'This process is not supervised, so it cannot restart itself: restart it the ' +
  'way this deployment is started.'

function bannerText(
  error: string | null,
  restarting: boolean,
  count: number,
  supervised: boolean,
): string {
  if (error !== null) return `Could not check whether a restart is needed: ${error}`
  if (restarting) return 'Restarting the backend...'
  const message =
    count === 1
      ? '1 setting requires a restart to take effect.'
      : `${count} settings require a restart to take effect.`
  return supervised ? message : `${message} ${UNSUPERVISED_NOTE}`
}

/**
 * Notice that saved settings are not in effect yet, and the control that puts
 * them in effect.
 *
 * Takes no props: what is pending comes from the backend, which knows which
 * restart-required settings were written after it booted. Counting them here
 * from whatever this tab happened to save would lose the notice on reload and
 * hide a restart another operator's save had already made necessary.
 *
 * The restart lives here rather than in a separate admin corner because this
 * is where the operator learns they need one. Without it the only way to apply
 * a documented, dashboard-editable setting is to leave the product and find a
 * shell, which makes the setting effectively unreachable through the interface
 * that offered it.
 */
export function RestartBanner() {
  const pending = useRestartStore((s) => s.pending)
  const supervised = useRestartStore((s) => s.supervised)
  const error = useRestartStore((s) => s.error)
  const restarting = useRestartStore((s) => s.restarting)
  const restart = useRestartStore((s) => s.restart)
  const refresh = useRestartStore((s) => s.refresh)

  useEffect(() => {
    void refresh()
  }, [refresh])

  const count = pending.length
  const visible = count > 0 || error !== null
  const canRestart = error === null && supervised

  return (
    <AnimatePresence>
      {visible && (
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
            {bannerText(error, restarting, count, supervised)}
          </span>
          {canRestart && (
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
        </motion.div>
      )}
    </AnimatePresence>
  )
}
