import { Dialog } from '@base-ui/react/dialog'
import { X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { cn } from '@/lib/utils'
import { useShortcutRegistry, type RegisteredShortcut } from '@/hooks/use-shortcut-registry'
import { Button } from './button'
import { KeyboardShortcutHint } from './keyboard-shortcut-hint'

export interface CommandCheatsheetProps {
  /** Controlled open state. When omitted, the component self-manages via `?` shortcut. */
  open?: boolean | undefined
  onOpenChange?: ((open: boolean) => void) | undefined
  /** Disable the global `?` shortcut (e.g. when a modal is already open). */
  disableShortcut?: boolean | undefined
  className?: string | undefined
}

type IdentifiedShortcut = { id: string } & RegisteredShortcut

function isEditable(el: Element | null): boolean {
  if (!el) return false
  const tag = el.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  return (el as HTMLElement).isContentEditable
}

function groupShortcuts(shortcuts: ReadonlyArray<IdentifiedShortcut>) {
  // Preserve the registry-assigned `id` on every row so React can key the
  // list deterministically (stable when two rows share a label + keys).
  const map = new Map<string, IdentifiedShortcut[]>()
  for (const s of shortcuts) {
    const list = map.get(s.group) ?? []
    list.push(s)
    map.set(s.group, list)
  }
  return [...map.entries()]
}

interface ShortcutSectionProps {
  group: string
  items: readonly IdentifiedShortcut[]
}

function ShortcutSection({ group, items }: ShortcutSectionProps) {
  return (
    <section>
      <h3 className="mb-2 text-[length:var(--so-text-compact)] font-semibold uppercase tracking-wider text-muted-foreground">
        {group}
      </h3>
      <ul className="space-y-1.5">
        {items.map((s) => (
          <li key={s.id} className="flex items-center justify-between gap-4 text-sm">
            <span className="text-foreground">{s.label}</span>
            <KeyboardShortcutHint keys={s.keys} size="md" />
          </li>
        ))}
      </ul>
    </section>
  )
}

/**
 * Full-screen keyboard-shortcut cheatsheet triggered by the `?` key.
 *
 * Reads registered shortcuts from `useShortcutRegistry`, groups by
 * `group` name, and renders a Base UI Dialog with shortcut rows. The
 * list updates live as routes change (shortcuts registered by mounted
 * components appear/disappear as pages enter/leave).
 */
export function CommandCheatsheet({
  open: openProp,
  onOpenChange,
  disableShortcut = false,
  className,
}: CommandCheatsheetProps) {
  const [internalOpen, setInternalOpen] = useState(false)
  const isControlled = openProp !== undefined
  const open = isControlled ? openProp : internalOpen

  const setOpen = useCallback(
    (next: boolean) => {
      if (!isControlled) setInternalOpen(next)
      onOpenChange?.(next)
    },
    [isControlled, onOpenChange],
  )

  const { shortcuts } = useShortcutRegistry()
  const grouped = useMemo(() => groupShortcuts(shortcuts), [shortcuts])

  useEffect(() => {
    if (disableShortcut) return
    const handler = (event: KeyboardEvent) => {
      if (event.key !== '?') return
      if (event.metaKey || event.ctrlKey || event.altKey) return
      if (isEditable(document.activeElement)) return
      event.preventDefault()
      setOpen(!open)
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [disableShortcut, open, setOpen])

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm transition-opacity duration-[var(--so-transition-default)] ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0" />
        <Dialog.Popup
          className={cn(
            'fixed top-1/2 left-1/2 z-50 w-full max-w-lg -translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border-bright bg-surface p-card shadow-[var(--so-shadow-card-hover)]',
            'transition-[opacity,translate,scale] duration-[var(--so-transition-default)] ease-out',
            'data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0',
            'data-[closed]:scale-95 data-[starting-style]:scale-95 data-[ending-style]:scale-95',
            'max-h-[80vh] overflow-hidden flex flex-col',
            className,
          )}
        >
          <div className="flex items-center justify-between border-b border-border pb-3 mb-3">
            <Dialog.Title className="text-base font-semibold text-foreground">
              Keyboard shortcuts
            </Dialog.Title>
            <Dialog.Close
              render={
                <Button size="icon-sm" variant="ghost" aria-label="Close">
                  <X className="size-3.5" aria-hidden="true" />
                </Button>
              }
            />
          </div>

          <div className="flex-1 overflow-y-auto pr-1">
            {grouped.length === 0 ? (
              <p className="py-4 text-center text-xs text-muted-foreground">
                No shortcuts registered on this page.
              </p>
            ) : (
              <div className="space-y-5">
                {grouped.map(([group, items]) => (
                  <ShortcutSection key={group} group={group} items={items} />
                ))}
              </div>
            )}
          </div>

          <p className="mt-3 border-t border-border pt-3 text-[length:var(--so-text-compact)] text-muted-foreground">
            Press <KeyboardShortcutHint keys={['?']} /> to toggle this panel.
          </p>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
