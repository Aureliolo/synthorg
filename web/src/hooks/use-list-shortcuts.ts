import { useEffect, useRef, useState } from 'react'

export interface UseListShortcutsOptions {
  /** Total number of items in the active list. */
  itemCount: number
  /** Handler when user presses Enter on the selected row. */
  onOpen?: (index: number) => void
  /** Handler when user presses `e` on the selected row (edit). */
  onEdit?: (index: number) => void
  /** Handler when user presses Delete or Backspace on the selected row (destructive). */
  onDelete?: (index: number) => void
  /** Handler for `/`: focus the list's search input. */
  onFocusSearch?: () => void
  /** Disable all shortcuts (e.g. when a modal is open). */
  disabled?: boolean
}

export interface UseListShortcutsResult {
  selectedIndex: number | null
  setSelectedIndex: (index: number | null) => void
}

/** Two-press window for the `gg` jump-to-top shortcut. */
const DOUBLE_G_WINDOW_MS = 500

function isEditable(el: Element | null): boolean {
  if (!el) return false
  const tag = el.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  return (el as HTMLElement).isContentEditable
}

interface ShortcutDeps {
  readonly itemCount: number
  readonly selectedIndex: number | null
  readonly lastGRef: React.RefObject<number>
  readonly setSelectedIndex: React.Dispatch<React.SetStateAction<number | null>>
  readonly onOpen?: ((index: number) => void) | undefined
  readonly onEdit?: ((index: number) => void) | undefined
  readonly onDelete?: ((index: number) => void) | undefined
  readonly onFocusSearch?: (() => void) | undefined
}

type ShortcutAction = (event: KeyboardEvent, deps: ShortcutDeps) => void

function _moveDown(event: KeyboardEvent, deps: ShortcutDeps): void {
  if (deps.itemCount === 0) return
  event.preventDefault()
  deps.setSelectedIndex((prev) =>
    prev === null ? 0 : Math.min(deps.itemCount - 1, prev + 1),
  )
}

function _moveUp(event: KeyboardEvent, deps: ShortcutDeps): void {
  if (deps.itemCount === 0) return
  event.preventDefault()
  deps.setSelectedIndex((prev) =>
    prev === null ? 0 : Math.max(0, prev - 1),
  )
}

function _jumpToTop(event: KeyboardEvent, deps: ShortcutDeps): void {
  const now = Date.now()
  if (now - deps.lastGRef.current >= DOUBLE_G_WINDOW_MS) {
    deps.lastGRef.current = now
    return
  }
  // Within the two-press window: reset the timer first (so a stale
  // `g` does not chain with a future single `g`) then jump.
  deps.lastGRef.current = 0
  if (deps.itemCount === 0) return
  event.preventDefault()
  deps.setSelectedIndex(0)
}

function _jumpToBottom(event: KeyboardEvent, deps: ShortcutDeps): void {
  if (deps.itemCount === 0) return
  event.preventDefault()
  deps.setSelectedIndex(deps.itemCount - 1)
}

function _invokeAt(
  event: KeyboardEvent,
  selectedIndex: number | null,
  callback: ((index: number) => void) | undefined,
): void {
  if (selectedIndex === null || !callback) return
  event.preventDefault()
  callback(selectedIndex)
}

function _focusSearchAction(event: KeyboardEvent, deps: ShortcutDeps): void {
  if (!deps.onFocusSearch) return
  event.preventDefault()
  deps.onFocusSearch()
}

/**
 * Key-to-action dispatch table. Adding a new shortcut is a one-line
 * insert here plus a small named handler above; the event handler in
 * the effect stays a constant-complexity table lookup.
 */
const KEY_TO_ACTION: Readonly<Record<string, ShortcutAction>> = {
  j: _moveDown,
  ArrowDown: _moveDown,
  k: _moveUp,
  ArrowUp: _moveUp,
  g: _jumpToTop,
  G: _jumpToBottom,
  Enter: (e, d) => _invokeAt(e, d.selectedIndex, d.onOpen),
  e: (e, d) => _invokeAt(e, d.selectedIndex, d.onEdit),
  Delete: (e, d) => _invokeAt(e, d.selectedIndex, d.onDelete),
  Backspace: (e, d) => _invokeAt(e, d.selectedIndex, d.onDelete),
  '/': _focusSearchAction,
}

function _isModifierEvent(event: KeyboardEvent): boolean {
  return event.metaKey || event.ctrlKey || event.altKey
}

function _clampSelection(
  prev: number | null,
  itemCount: number,
): number | null {
  if (prev === null) return null
  if (itemCount <= 0) return null
  if (prev >= itemCount) return itemCount - 1
  return prev
}

/**
 * Keyboard shortcuts for list pages.
 *
 * Registered shortcuts (when no input is focused and `disabled` is false):
 * - `j` / `ArrowDown`: select next item
 * - `k` / `ArrowUp`: select previous item
 * - `g g`: select first item (press `g` twice within 500ms)
 * - `Shift+G`: select last item
 * - `Enter`: invoke `onOpen`
 * - `e`: invoke `onEdit`
 * - `Delete` / `Backspace`: invoke `onDelete`
 * - `/`: invoke `onFocusSearch`
 */
export function useListShortcuts({
  itemCount,
  onOpen,
  onEdit,
  onDelete,
  onFocusSearch,
  disabled = false,
}: UseListShortcutsOptions): UseListShortcutsResult {
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null)
  const lastGRef = useRef<number>(0)

  // Clamp the selection when the list shrinks (or becomes empty) so we never
  // keep a stale index pointing past the end of the array. The functional
  // updater lets React coalesce no-op updates.
  useEffect(() => {
    // eslint-disable-next-line @eslint-react/set-state-in-effect -- itemCount-driven reconciliation, not a derived-state anti-pattern
    setSelectedIndex((prev) => _clampSelection(prev, itemCount))
  }, [itemCount])

  useEffect(() => {
    if (disabled) return
    const handler = (event: KeyboardEvent): void => {
      if (_isModifierEvent(event)) return
      if (isEditable(document.activeElement)) return
      const action = KEY_TO_ACTION[event.key]
      if (!action) return
      const deps: ShortcutDeps = {
        itemCount,
        selectedIndex,
        lastGRef,
        setSelectedIndex,
        onOpen,
        onEdit,
        onDelete,
        onFocusSearch,
      }
      action(event, deps)
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [disabled, itemCount, onDelete, onEdit, onFocusSearch, onOpen, selectedIndex])

  return { selectedIndex, setSelectedIndex }
}
