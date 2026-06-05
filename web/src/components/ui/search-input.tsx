import { Search, X } from 'lucide-react'
import { useEffect, useImperativeHandle, useRef, type RefObject } from 'react'
import { cn } from '@/lib/utils'

export interface SearchInputHandle {
  focus: () => void
  clear: () => void
}

export type SearchInputWidth = 'narrow' | 'wide'

const SEARCH_WIDTH_STYLE: Record<SearchInputWidth, string> = {
  narrow: 'var(--so-search-max-narrow)',
  wide: 'var(--so-search-max-wide)',
}

export interface SearchInputProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  /** Accessible label (required when no visible label is rendered). */
  ariaLabel?: string
  /**
   * Enable the global `/` shortcut to focus this input. Default false. Ignored
   * when the active element is already inside an input / textarea / contenteditable.
   * Set to true on the primary list page search; leave false on nested searches.
   */
  focusShortcut?: boolean
  disabled?: boolean
  /**
   * Maximum width cap. `'wide'` (default) suits list-page primary search;
   * `'narrow'` suits compact contexts like settings search. Mapped to
   * `--so-search-max-*` tokens so the cap adapts with theme density.
   */
  maxWidth?: SearchInputWidth
  className?: string
  /** React 19 style ref that exposes `focus` and `clear` imperative methods. */
  ref?: RefObject<SearchInputHandle | null>
}

function isEditable(el: Element | null): boolean {
  if (!el) return false
  const tag = el.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  return (el as HTMLElement).isContentEditable
}

export function SearchInput({
  value,
  onChange,
  placeholder = 'Search...',
  ariaLabel = 'Search',
  focusShortcut = false,
  disabled,
  maxWidth = 'wide',
  className,
  ref,
}: SearchInputProps) {
  const inputRef = useRef<HTMLInputElement | null>(null)

  useImperativeHandle(ref, () => ({
    focus: () => inputRef.current?.focus(),
    clear: () => onChange(''),
  }), [onChange])

  useEffect(() => {
    if (!focusShortcut) return
    if (disabled) return
    const handler = (event: KeyboardEvent) => {
      if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey) return
      if (isEditable(document.activeElement)) return
      event.preventDefault()
      inputRef.current?.focus()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [focusShortcut, disabled])

  return (
    <div
      className={cn('relative w-full', className)}
      style={{ maxWidth: SEARCH_WIDTH_STYLE[maxWidth] }}
    >
      <Search
        className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
        aria-hidden="true"
      />
      <input
        ref={inputRef}
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={ariaLabel}
        disabled={disabled}
        className={cn(
          'h-9 w-full rounded-lg border border-border bg-card pl-9 pr-9 text-sm text-foreground placeholder:text-muted-foreground',
          'focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/40',
          'disabled:opacity-60 disabled:cursor-not-allowed',
        )}
      />
      {value && !disabled && (
        <button
          type="button"
          onClick={() => {
            onChange('')
            inputRef.current?.focus()
          }}
          aria-label="Clear search"
          className="absolute right-2.5 top-1/2 flex size-5 -translate-y-1/2 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <X className="size-3.5" aria-hidden="true" />
        </button>
      )}
    </div>
  )
}
