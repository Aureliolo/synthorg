import { StatusPill, type StatusPillTone } from '@/components/ui/status-pill'
import { formatLabel } from '@/utils/format'
import type { SubsystemPhase, SubsystemReport } from '@/api/types/subsystems'

/**
 * How each phase reads as a pill.
 *
 * Exhaustive over the generated union so a phase added to the backend enum is a
 * compile error here rather than a row that renders untoned.
 */
const PHASE_TONES: Record<SubsystemPhase, StatusPillTone> = {
  active: 'success',
  // Off because an operator turned it off: the deployment is behaving as
  // configured, so it is neither a fault nor a success.
  disabled: 'text-secondary',
  degraded: 'warning',
  waiting: 'warning',
  rebuilding: 'warning',
  blocked: 'warning',
  failed: 'danger',
  unreachable: 'danger',
}

/** Why this one is not up, in the reconciler's own words. */
function phaseDetail(report: SubsystemReport): string | null {
  const waiting =
    report.waiting_on.length > 0
      ? `waiting on ${report.waiting_on.map(formatLabel).join(', ')}`
      : null
  return [report.detail, waiting].filter((part) => part !== null).join(' -- ') || null
}

export interface SubsystemPhaseListProps {
  /** Every declared subsystem, in the order the reconciler activates them. */
  subsystems: readonly SubsystemReport[]
  /** Why the list could not be read, when it could not. */
  error: string | null
}

/**
 * Every declared subsystem and the phase it is in.
 *
 * The blockers panel answers "what stands between the org and progress" and so
 * lists only what is not up. That leaves the complement, "what is wired at all",
 * readable nowhere but `GET /subsystems`, and an operator cannot tell a
 * subsystem that activated from one this build never declared.
 *
 * Activation order is kept rather than sorted worst-first: it is the order the
 * reconciler works in, so a run of waiting rows below a failed one reads as the
 * consequence it usually is.
 */
export function SubsystemPhaseList({ subsystems, error }: SubsystemPhaseListProps) {
  const active = subsystems.filter((report) => report.phase === 'active').length
  return (
    <section className="mt-6 border-t border-border pt-4">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold text-foreground">Declared subsystems</h3>
        {subsystems.length > 0 && (
          <span className="text-compact text-muted-foreground">
            {active} of {subsystems.length} active
          </span>
        )}
      </div>
      {error !== null && (
        <p role="alert" className="mt-2 text-compact text-danger">
          {/* An empty list after a failed read is not an org with no subsystems,
              and rendering it as one would be the more confident of the two
              wrong answers. */}
          Could not read the subsystem list: {error}
        </p>
      )}
      {error === null && subsystems.length === 0 && (
        <p className="mt-2 text-compact text-muted-foreground">
          Not read yet.
        </p>
      )}
      <ul className="mt-2 flex flex-col gap-1.5">
        {subsystems.map((report) => {
          const detail = phaseDetail(report)
          return (
            <li
              key={report.name}
              className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1"
            >
              <span className="text-compact text-foreground">
                {formatLabel(report.name)}
              </span>
              <span className="flex items-center gap-2">
                {detail !== null && (
                  <span className="text-compact text-muted-foreground">{detail}</span>
                )}
                <StatusPill tone={PHASE_TONES[report.phase]}>{report.phase}</StatusPill>
              </span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
