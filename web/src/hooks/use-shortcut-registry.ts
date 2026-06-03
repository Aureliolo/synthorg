import { createContext, use, useEffect, useId } from 'react'

export interface RegisteredShortcut {
  /** Keys pressed in sequence, rendered as `<kbd>` pills. Example: `['Ctrl', 'K']`. */
  keys: string[]
  /** Human-readable action description. */
  label: string
  /** Logical grouping used by `<CommandCheatsheet>`. Pages should supply a consistent group name. */
  group: string
}

export interface ShortcutRegistryContextValue {
  shortcuts: ReadonlyArray<{ id: string } & RegisteredShortcut>
  register: (id: string, shortcuts: RegisteredShortcut[]) => void
  unregister: (id: string) => void
}

export const ShortcutRegistryContext = createContext<ShortcutRegistryContextValue | null>(null)

export function useShortcutRegistry() {
  const ctx = use(ShortcutRegistryContext)
  if (!ctx) {
    throw new Error('useShortcutRegistry must be used inside <ShortcutRegistryProvider>')
  }
  return ctx
}

/**
 * Register a set of shortcuts while this component is mounted. Shortcuts
 * are exposed via `useShortcutRegistry().shortcuts` for `<CommandCheatsheet>`
 * to display. This hook does NOT attach any keyboard handlers -- registration
 * is documentation/display-only. Handlers are wired separately (via
 * `useListShortcuts`, `useCommandPalette`, etc.).
 *
 * Unlike `useShortcutRegistry()`, this hook gracefully no-ops when used
 * outside `<ShortcutRegistryProvider>` (the effect body short-circuits when
 * `register` / `unregister` are undefined). That lets shortcut-registering
 * components be reused in isolated contexts (unit tests, isolated Storybook
 * stories) without forcing every caller to mount the provider.
 */
export function useRegisterShortcuts(shortcuts: RegisteredShortcut[]) {
  const ctx = use(ShortcutRegistryContext)
  // Destructure the stable callbacks so the effect does not re-run every
  // time the context value object identity changes. Depending on `ctx`
  // directly would create an infinite loop: register -> provider re-renders
  // -> ctx identity changes -> effect re-runs -> register again -> ...
  const register = ctx?.register
  const unregister = ctx?.unregister
  const ownerId = useId()
  // Content-based change key: consumers that build `shortcuts` inline pass a
  // fresh array identity each render, so we re-register on content change only
  // (an unmemoised array of the same content does not thrash the registry).
  const shortcutsKey = JSON.stringify(shortcuts)
  useEffect(() => {
    if (!register || !unregister) return
    register(ownerId, shortcuts)
    return () => unregister(ownerId)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `shortcuts` is keyed by content via `shortcutsKey`, not identity, to avoid re-registration thrash on inline arrays
  }, [register, unregister, ownerId, shortcutsKey])
}
