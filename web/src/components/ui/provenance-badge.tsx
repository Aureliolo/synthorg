import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

export interface ProvenanceBadgeProps {
  /** Tone classes (border/background/text) selected by the caller's kind map. */
  className?: string
  /** Native tooltip describing the provenance the badge conveys. */
  title?: string
  children: ReactNode
}

/**
 * Minimal presentational badge for data-provenance labels (measured vs
 * absent, and future provenance kinds). The caller owns the kind→tone/label
 * mapping and passes the tone via ``className``; this primitive owns only the
 * shared span skeleton so provenance badges never re-implement it inline.
 */
export function ProvenanceBadge({ className, title, children }: ProvenanceBadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium',
        className,
      )}
      title={title}
    >
      {children}
    </span>
  )
}
