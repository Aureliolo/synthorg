import { Command } from 'cmdk-base'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Search } from 'lucide-react'
import { cn } from '@/lib/utils'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import type { CommandItem } from '@/hooks/useCommandPalette'
import { useCommandPalette } from '@/hooks/useCommandPalette'
import { useToastStore } from '@/stores/toast'

const log = createLogger('CommandPalette')

const RECENT_STORAGE_KEY = 'so_recent_commands'
const MAX_RECENT = 5

function getRecentIds(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    const VALID_ID = /^[\w\-:.]+$/
    return parsed
      .filter((v): v is string => typeof v === 'string' && v.length <= 64 && VALID_ID.test(v))
      .slice(0, MAX_RECENT)
  } catch (err) {
    log.warn('Failed to read recent commands from localStorage', err)
    return []
  }
}

function addRecentId(id: string) {
  try {
    const recent = getRecentIds().filter((r) => r !== id)
    recent.unshift(id)
    localStorage.setItem(RECENT_STORAGE_KEY, JSON.stringify(recent.slice(0, MAX_RECENT)))
  } catch (err) {
    // Best-effort convenience feature -- never block command execution, but
    // surface the diagnostic so quota/security errors can be correlated with
    // "my recent commands stopped persisting" bug reports.
    log.debug('Failed to persist recent commands to localStorage', err)
  }
}

export interface CommandPaletteProps {
  className?: string
}

export function CommandPalette({ className }: CommandPaletteProps) {
  const { commands, isOpen, close, toggle, setOpen } = useCommandPalette()
  const [search, setSearch] = useState('')
  const [scope, setScope] = useState<'global' | 'local'>('global')

  // Cmd+K / Ctrl+K global toggle. Escape is handled by Base UI Dialog inside
  // cmdk-base's Command.Dialog, so we no longer need a manual Escape handler.
  //
  // Uses toLowerCase() to match both 'k' and 'K' -- with Caps Lock on (or
  // AZERTY layouts that remap the key), `e.key` reports 'K' for the same
  // physical keystroke. The sibling useSettingsKeyboard.ts hook follows
  // the same convention for its Ctrl+S/ handlers.
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key.toLowerCase() === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        toggle()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [toggle])

  // Reset search and scope when palette opens
  useEffect(() => {
    if (isOpen) {
      setSearch('') // eslint-disable-line @eslint-react/set-state-in-effect -- intentional reset on open
      setScope('global') // eslint-disable-line @eslint-react/set-state-in-effect -- intentional reset on open
    }
  }, [isOpen])

  const filteredCommands = useMemo(() => {
    return commands.filter((cmd) => {
      const cmdScope = cmd.scope ?? 'global'
      return scope === 'global' ? true : cmdScope === 'local'
    })
  }, [commands, scope])

  const grouped = useMemo(() => {
    const groups = new Map<string, CommandItem[]>()
    for (const cmd of filteredCommands) {
      const existing = groups.get(cmd.group) ?? []
      existing.push(cmd)
      groups.set(cmd.group, existing)
    }
    return groups
  }, [filteredCommands])

  // Recent items (only shown when search is empty)
  // Re-read recent items each time the palette opens
  const recentIds = useMemo(() => getRecentIds(), [isOpen]) // eslint-disable-line @eslint-react/exhaustive-deps
  const recentItems = useMemo(() => {
    if (search) return []
    return recentIds
      .map((id) => commands.find((c) => c.id === id))
      .filter((c): c is CommandItem => {
        if (c === undefined) return false
        const cmdScope = c.scope ?? 'global'
        return scope === 'global' ? true : cmdScope === 'local'
      })
  }, [search, recentIds, commands, scope])

  const recentIdSet = useMemo(
    () => new Set(recentItems.map((c) => c.id)),
    [recentItems],
  )

  const handleSelect = useCallback(
    async (cmd: CommandItem) => {
      addRecentId(cmd.id)
      try {
        // Await via Promise.resolve so both sync and async actions are
        // handled. A promise rejection from an async action would otherwise
        // escape the try/catch and close() below would run as if the action
        // succeeded -- the opposite of the "user sees failures" contract.
        await Promise.resolve(cmd.action())
      } catch (err) {
        // Always log + toast so users see when a destructive command (e.g.
        // "Delete agent") fails instead of the palette closing silently as
        // if the action succeeded. Getting this wrong would mean users
        // believe destructive actions completed when they did not.
        log.error('Command action failed', { commandId: cmd.id, label: cmd.label }, err)
        useToastStore.getState().add({
          variant: 'error',
          title: 'Command failed',
          description: `"${cmd.label}" did not complete: ${getErrorMessage(err)}`,
        })
      } finally {
        // Close in finally so the palette dismisses after the action
        // resolves (or rejects), not before the awaited work completes.
        close()
      }
    },
    [close],
  )

  const handleScopeToggle = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Tab' && commands.some((c) => c.scope === 'local')) {
        e.preventDefault()
        setScope((prev) => (prev === 'global' ? 'local' : 'global'))
      }
    },
    [commands],
  )

  const hasLocalCommands = commands.some((c) => c.scope === 'local')

  return (
    <Command.Dialog
      open={isOpen}
      onOpenChange={setOpen}
      label="Command palette"
      overlayClassName="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm transition-opacity duration-150 ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0"
      contentClassName={cn(
        'fixed left-1/2 top-[15vh] z-50 w-full max-w-[640px] max-[1023px]:max-w-[calc(100vw-var(--so-space-8))] -translate-x-1/2',
        'rounded-xl border border-border-bright bg-surface shadow-[var(--so-shadow-card-hover)]',
        // Tailwind v4 uses dedicated `translate:`/`scale:` CSS properties
        // rather than the legacy `transform:` shorthand, so the transition
        // property list must name each one explicitly for the animation to
        // play.
        'transition-[opacity,translate] duration-150 ease-out',
        'data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0',
        'data-[closed]:-translate-y-2 data-[starting-style]:-translate-y-2 data-[ending-style]:-translate-y-2',
        className,
      )}
      onKeyDown={handleScopeToggle}
    >
      {/* Search input */}
      <div className="flex items-center gap-3 border-b border-border px-4">
        <Search className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <Command.Input
          value={search}
          onValueChange={setSearch}
          placeholder={scope === 'global' ? 'Search commands...' : 'Search page commands...'}
          className="flex-1 bg-transparent py-3 text-base text-foreground placeholder:text-muted-foreground outline-none"
        />
        {hasLocalCommands && (
          <span className="shrink-0 rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">
            Tab: {scope}
          </span>
        )}
      </div>

      <Command.List className="max-h-[320px] overflow-y-auto p-2">
        <Command.Empty className="py-6 text-center text-sm text-muted-foreground">
          No results found.
        </Command.Empty>

        {/* Recent items */}
        {recentItems.length > 0 && (
          <Command.Group heading="Recent" className="mb-1">
            {recentItems.map((cmd) => (
              <CommandItemRow key={`recent-${cmd.id}`} item={cmd} onSelect={handleSelect} />
            ))}
          </Command.Group>
        )}

        {/* Command groups */}
        {[...grouped.entries()].map(([groupName, items]) => (
          <Command.Group
            key={groupName}
            heading={groupName}
            className="mb-1"
          >
            {items.filter((cmd) => !recentIdSet.has(cmd.id)).map((cmd) => (
              <CommandItemRow key={cmd.id} item={cmd} onSelect={handleSelect} />
            ))}
          </Command.Group>
        ))}
      </Command.List>

      {/* Footer hint */}
      <div className="flex items-center gap-4 border-t border-border px-4 py-2 text-[10px] text-muted-foreground">
        <span>
          <kbd className="rounded border border-border px-1">Enter</kbd> select
        </span>
        <span>
          <kbd className="rounded border border-border px-1">Esc</kbd> close
        </span>
        {hasLocalCommands && (
          <span>
            <kbd className="rounded border border-border px-1">Tab</kbd> scope
          </span>
        )}
      </div>
    </Command.Dialog>
  )
}

function CommandItemRow({
  item,
  onSelect,
}: {
  item: CommandItem
  onSelect: (item: CommandItem) => void
}) {
  const Icon = item.icon
  return (
    <Command.Item
      value={[item.label, ...(item.keywords ?? [])].join(' ')}
      onSelect={() => onSelect(item)}
      className="flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-sm text-foreground data-[selected=true]:bg-card-hover"
    >
      {Icon && <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />}
      <div className="flex-1">
        <span>{item.label}</span>
        {item.description && (
          <span className="ml-2 text-xs text-muted-foreground">{item.description}</span>
        )}
      </div>
      {item.shortcut && (
        <div className="flex gap-1">
          {item.shortcut.map((key) => (
            <kbd
              key={key}
              className="rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground"
            >
              {key}
            </kbd>
          ))}
        </div>
      )}
    </Command.Item>
  )
}
