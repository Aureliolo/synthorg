import { cn } from '@/lib/utils'

export interface CapabilityPillProps {
  /** Capability name shown in the pill (e.g. "tools", "vision"). */
  label: string
  /** Tone classes (background/text) selected by the caller's colour map. */
  className: string
  /**
   * Whether an operator override, not the resolved card/probe value, is
   * what set this capability. Marked visibly (a ring + a "*") and to
   * screen readers (a hidden "(operator override)" suffix), never by
   * colour alone.
   */
  overridden: boolean
}

/** One capability pill in a model's capability list, e.g. in `ProviderModelList`. */
export function CapabilityPill({ label, className, overridden }: CapabilityPillProps) {
  return (
    <span
      title={overridden ? `${label}: set by an operator override` : undefined}
      className={cn(
        'rounded px-1.5 py-0.5 text-micro font-medium leading-tight',
        overridden && 'ring-1 ring-inset ring-current',
        className,
      )}
    >
      {label}
      {overridden && <span aria-hidden="true"> *</span>}
      {overridden && <span className="sr-only"> (operator override)</span>}
    </span>
  )
}
