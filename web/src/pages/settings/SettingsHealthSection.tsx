import { useCallback, useRef } from 'react'

import type { SettingNamespace } from '@/api/types/settings'
import { cn } from '@/lib/utils'
import { NAMESPACE_DISPLAY_NAMES } from '@/utils/constants'

export interface NamespaceTabBarProps {
  namespaces: readonly SettingNamespace[]
  activeNamespace: SettingNamespace | null
  onSelect: (ns: SettingNamespace | null) => void
  namespaceCounts: ReadonlyMap<string, number>
  namespaceIcons?: Partial<Record<SettingNamespace, React.ReactNode>>
}

export function NamespaceTabBar({
  namespaces,
  activeNamespace,
  onSelect,
  namespaceCounts,
  namespaceIcons,
}: NamespaceTabBarProps) {
  const tablistRef = useRef<HTMLDivElement>(null)

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const container = tablistRef.current
      if (!container) return
      const tabs = Array.from(container.querySelectorAll<HTMLElement>('button'))
      const current = tabs.findIndex((t) => t === document.activeElement)
      if (current === -1) return

      let target: number
      if (e.key === 'ArrowRight') target = (current + 1) % tabs.length
      else if (e.key === 'ArrowLeft') target = (current - 1 + tabs.length) % tabs.length
      else if (e.key === 'Home') target = 0
      else if (e.key === 'End') target = tabs.length - 1
      else return

      e.preventDefault()
      tabs[target]?.focus()
    },
    [],
  )

  const visibleNamespaces = namespaces.filter((ns) => (namespaceCounts.get(ns) ?? 0) > 0)

  return (
    <div
      ref={tablistRef}
      className="flex flex-wrap items-center gap-1 rounded-lg border border-border bg-card px-2 py-1.5"
      role="toolbar"
      aria-label="Setting namespace filter"
      onKeyDown={handleKeyDown}
    >
      <button
        type="button"
        aria-pressed={activeNamespace === null}
        onClick={() => onSelect(null)}
        className={cn(
          'inline-flex items-center gap-1.5 rounded-md px-3 py-2 text-xs font-semibold transition-all duration-200',
          activeNamespace === null
            ? 'bg-accent/10 text-accent'
            : 'text-text-secondary hover:bg-card-hover hover:text-foreground',
        )}
      >
        All
      </button>
      {visibleNamespaces.map((ns) => {
        const count = namespaceCounts.get(ns) ?? 0
        const icon = namespaceIcons?.[ns]
        return (
          <button
            key={ns}
            type="button"
            aria-pressed={activeNamespace === ns}
            onClick={() => onSelect(ns)}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-md px-3 py-2 text-xs font-semibold transition-all duration-200',
              activeNamespace === ns
                ? 'bg-accent/10 text-accent'
                : 'text-text-secondary hover:bg-card-hover hover:text-foreground',
            )}
          >
            {icon != null && icon !== false && (
              <span className="shrink-0">{icon}</span>
            )}
            {NAMESPACE_DISPLAY_NAMES[ns]}
            <span className="font-normal text-text-muted">{count}</span>
          </button>
        )
      })}
    </div>
  )
}
