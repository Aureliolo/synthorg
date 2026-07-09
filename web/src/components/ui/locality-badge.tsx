export interface LocalityBadgeProps {
  /** Whether the associated provider is locally hosted (free to run). */
  isLocal: boolean
}

/** Flags an agent whose model runs on a local, free-to-run provider. */
export function LocalityBadge({ isLocal }: LocalityBadgeProps) {
  if (!isLocal) return null
  return (
    <span
      className="inline-flex shrink-0 rounded-full border border-success/20 bg-success/8 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-success"
      title="Runs on a local provider -- free to run"
    >
      local
    </span>
  )
}
