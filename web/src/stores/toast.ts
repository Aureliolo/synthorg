import { create } from 'zustand'

export type ToastVariant = 'success' | 'error' | 'warning' | 'info'

/**
 * Optional inline action surfaced inside a toast (e.g. a "Retry" button
 * on a connection-lost notification). The toast is always dismissed
 * after the action runs so the user gets immediate feedback.
 */
export interface ToastAction {
  label: string
  onClick: () => void
}

export interface ToastItem {
  id: string
  variant: ToastVariant
  title: string
  description?: string
  /** Auto-dismiss duration in ms (default: 5000). */
  duration?: number
  /** Whether the toast can be manually dismissed (default: true). */
  dismissible?: boolean
  /** Optional inline action (e.g. Retry, Undo). */
  action?: ToastAction
}

interface ToastState {
  toasts: ToastItem[]
  add: (toast: Omit<ToastItem, 'id'>) => string
  dismiss: (id: string) => void
  dismissAll: () => void
  /**
   * Clear all pending auto-dismiss timers without mutating the toasts array.
   * Intended for test teardown where the toast list is still needed for
   * assertions but pending setTimeout handles would otherwise leak past the
   * test boundary.
   */
  cancelAllPending: () => void
}

/** Variant-specific auto-dismiss durations. Warning/error are persistent (no auto-dismiss). */
const VARIANT_DURATIONS: Record<ToastVariant, number | null> = {
  success: 3000,
  info: 5000,
  warning: null,
  error: null,
}

/** Module-scoped timer map for auto-dismiss cleanup. */
const timers = new Map<string, ReturnType<typeof setTimeout>>()

let nextId = 0

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],

  add(toast) {
    const id = String(++nextId)
    const duration = toast.duration ?? VARIANT_DURATIONS[toast.variant]

    set((state) => ({
      toasts: [...state.toasts, { ...toast, id }],
    }))

    // Schedule auto-dismiss; a null duration (warning/error) stays persistent.
    if (duration !== null) {
      const timer = setTimeout(() => {
        timers.delete(id)
        get().dismiss(id)
      }, duration)
      timers.set(id, timer)
    }

    return id
  },

  dismiss(id) {
    // Clear auto-dismiss timer if it exists
    const timer = timers.get(id)
    if (timer !== undefined) {
      clearTimeout(timer)
      timers.delete(id)
    }

    set((state) => ({
      toasts: state.toasts.filter((t) => t.id !== id),
    }))
  },

  dismissAll() {
    // Clear all timers
    for (const timer of timers.values()) {
      clearTimeout(timer)
    }
    timers.clear()

    set({ toasts: [] })
  },

  cancelAllPending() {
    for (const timer of timers.values()) {
      clearTimeout(timer)
    }
    timers.clear()
  },
}))
