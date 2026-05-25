/** Shared button-style for sidebar action rows (collapse, notifications, command palette). */

import { cn } from '@/lib/utils'

export const SIDEBAR_BUTTON_CLASS = cn(
  'flex items-center gap-3 rounded-md px-3 py-2 text-sm',
  'text-text-secondary transition-colors',
  'hover:bg-card-hover hover:text-foreground',
)
