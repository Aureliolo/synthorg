import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export interface KeyboardShortcutHintProps {
  /** Keys to render as `<kbd>` pills, in order. Example: `['Ctrl', 'K']`. */
  keys: readonly string[]
  /** Optional trailing label rendered next to the keys (e.g. "to search"). */
  label?: ReactNode
  /** Visual density. `sm` for tooltips, `md` for cheatsheet rows. Default 'sm'. */
  size?: 'sm' | 'md'
  className?: string
}

type HintSize = NonNullable<KeyboardShortcutHintProps['size']>

function kbdSizeClasses(size: HintSize): string {
  return size === 'sm'
    ? 'text-[length:var(--so-text-micro)] h-5 min-w-5 px-1'
    : 'text-xs h-6 min-w-6 px-1.5'
}

interface KbdKeyProps {
  label: string
  size: HintSize
}

function KbdKey({ label, size }: KbdKeyProps) {
  return (
    <kbd
      className={cn(
        'inline-flex items-center justify-center rounded border border-border bg-surface font-mono font-medium text-foreground shadow-sm',
        kbdSizeClasses(size),
      )}
    >
      {label}
    </kbd>
  )
}

/**
 * Inline pill hint displaying a keyboard shortcut.
 *
 * Each key renders as a semantic `<kbd>` element with design-token styling.
 * Screen-readers hear the keys as-is; sighted users see the styled pills.
 * For discoverability, pair with `<CommandCheatsheet>` (triggered by `?`).
 */
export function KeyboardShortcutHint({
  keys,
  label,
  size = 'sm',
  className,
}: KeyboardShortcutHintProps) {
  return (
    <span className={cn('inline-flex items-center gap-1 text-muted-foreground', className)}>
      {keys.map((key, idx) => (
        <KbdKey
          // Keys render in input order; duplicates like ['g', 'g'] are valid,
          // so the positional index IS the identity here.
          // eslint-disable-next-line @eslint-react/no-array-index-key
          key={`${key}-${idx}`}
          label={key}
          size={size}
        />
      ))}
      {label !== undefined && label !== '' && (
        <span className={cn(size === 'sm' ? 'text-[length:var(--so-text-micro)]' : 'text-xs')}>{label}</span>
      )}
    </span>
  )
}
