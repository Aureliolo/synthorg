import { useCallback, useRef, type KeyboardEvent, type RefObject } from 'react'

/**
 * ARIA toolbar keyboard navigation.
 *
 * Returns a ref to attach to the container with ``role="toolbar"`` and
 * an ``onKeyDown`` handler that moves focus among the container's
 * focusable children on Arrow (up/down/left/right), ``Home``, and
 * ``End``. The hook only owns those keys -- it does not implement
 * roving tabindex, so Tab behavior remains native (driven by each
 * child's tabbability, ``disabled`` state, and any composite-widget
 * interception). Consumers that need Tab to skip past the toolbar
 * should manage that with ``tabIndex`` on the children themselves.
 */
export interface ToolbarKeyboardNav<T extends HTMLElement> {
  ref: RefObject<T | null>
  onKeyDown: (event: KeyboardEvent<T>) => void
}

const FOCUSABLE_SELECTOR = [
  'button:not([disabled])',
  '[href]',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function _hasModifier<T extends HTMLElement>(event: KeyboardEvent<T>): boolean {
  return event.ctrlKey || event.metaKey || event.altKey || event.shiftKey
}

/**
 * True when arrow / Home / End should belong to the focused element
 * rather than the toolbar (caret nav inside a text input, content-
 * editable region, etc.).
 */
function _isEditableElement(el: Element | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  const tag = el.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  return el.isContentEditable
}

type IndexFn = (activeIndex: number, count: number) => number

/** Forward wrap: -1 starts from the first item; everywhere else moves cyclically. */
const _forward: IndexFn = (i, n) => (i < 0 ? 0 : (i + 1) % n)
/** Backward wrap: -1 starts from the last item; everywhere else moves cyclically. */
const _backward: IndexFn = (i, n) => (i < 0 ? n - 1 : (i - 1 + n) % n)
const _first: IndexFn = () => 0
const _last: IndexFn = (_i, n) => n - 1

const TOOLBAR_INDEX_FNS: Readonly<Record<string, IndexFn>> = {
  ArrowRight: _forward,
  ArrowDown: _forward,
  ArrowLeft: _backward,
  ArrowUp: _backward,
  Home: _first,
  End: _last,
}

/**
 * Cyclic index of the next focusable child given an arrow / Home / End
 * key. Returns null for any other key so the caller knows to skip the
 * focus move. `activeIndex < 0` means no toolbar child is currently
 * focused, so a forward key lands on the first item and a backward key
 * on the last.
 */
function _nextToolbarIndex(
  key: string,
  activeIndex: number,
  count: number,
): number | null {
  const fn = TOOLBAR_INDEX_FNS[key]
  if (!fn) return null
  return fn(activeIndex, count)
}

function _collectToolbarItems(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
  ).filter((el) => !el.hasAttribute('data-toolbar-skip'))
}

export function useToolbarKeyboardNav<
  T extends HTMLElement = HTMLDivElement,
>(): ToolbarKeyboardNav<T> {
  const ref = useRef<T | null>(null)

  const onKeyDown = useCallback((event: KeyboardEvent<T>) => {
    const container = ref.current
    if (!container) return
    // Let nested composite controls own the event when they call
    // preventDefault themselves (e.g. a Menu or Combobox inside the
    // toolbar). Without this guard the toolbar would re-handle the
    // arrow key and move focus away mid-interaction.
    if (event.defaultPrevented) return
    if (_hasModifier(event)) return
    if (_isEditableElement(document.activeElement)) return
    const items = _collectToolbarItems(container)
    if (items.length === 0) return
    const activeIndex = items.indexOf(document.activeElement as HTMLElement)
    const nextIndex = _nextToolbarIndex(event.key, activeIndex, items.length)
    if (nextIndex === null) return
    event.preventDefault()
    items[nextIndex]?.focus()
  }, [])

  return { ref, onKeyDown }
}
