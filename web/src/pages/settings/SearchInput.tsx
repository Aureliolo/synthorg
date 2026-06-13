import { useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { Search, X } from 'lucide-react'
import { cn } from '@/lib/utils'

const DEBOUNCE_MS = 200

export interface SearchInputHandle {
  focus: () => void
}

export interface SearchInputProps {
  value: string
  onChange: (query: string) => void
  className?: string | undefined
  ref?: React.Ref<SearchInputHandle> | undefined
  resultCount?: number | undefined
}

export function SearchInput({ value, onChange, className, ref, resultCount }: SearchInputProps) {
  const [local, setLocal] = useState(value)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useImperativeHandle(ref, () => ({
    focus: () => inputRef.current?.focus(),
  }))

  // Sync external value changes (e.g. clearing from parent)
  const prevValueRef = useRef(value)
  if (value !== prevValueRef.current) {
    prevValueRef.current = value
    setLocal(value)
  }

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [])

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const next = e.target.value
      setLocal(next)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => onChange(next), DEBOUNCE_MS)
    },
    [onChange],
  )

  const handleClear = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    setLocal('')
    onChange('')
  }, [onChange])

  return (
    <div className={cn('relative', className)}>
      <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-text-muted" aria-hidden />
      <input
        ref={inputRef}
        type="text"
        value={local}
        onChange={handleChange}
        placeholder="Search settings..."
        className={cn(
          'h-9 w-full rounded-md border border-border bg-surface pl-9 text-sm text-foreground outline-none placeholder:text-text-muted focus:border-accent focus:ring-1 focus:ring-accent',
          local === value && resultCount !== undefined ? 'pr-20' : 'pr-8',
        )}
        aria-label="Search settings"
      />
      {local === value && resultCount !== undefined && (
        <span
          className="absolute right-8 top-1/2 -translate-y-1/2 text-micro text-text-muted"
          role="status"
          aria-live="polite"
        >
          {resultCount} {resultCount === 1 ? 'result' : 'results'}
        </span>
      )}
      {local && (
        <button
          type="button"
          onClick={handleClear}
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-text-muted transition-colors hover:text-foreground"
          aria-label="Clear search"
        >
          <X className="size-3.5" />
        </button>
      )}
    </div>
  )
}
