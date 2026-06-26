import { create } from 'zustand'
import { getSetupStatus } from '@/api/endpoints/setup'

interface SetupState {
  /** Whether initial setup is complete. `null` means not yet fetched. */
  setupComplete: boolean | null
  loading: boolean
  /** Whether the last fetch attempt failed. */
  error: boolean
  fetchSetupStatus: () => Promise<void>
  /** Mark setup complete locally after the backend confirms completion. */
  markSetupComplete: () => void
}

export const useSetupStore = create<SetupState>()((set, get) => ({
  // The dev auth bypass only skips the login screen; it must NOT fake setup
  // completion. Start unknown so the guard always reflects the backend's real
  // ``needs_setup`` (the wizard stays reachable in dev mode).
  setupComplete: null,
  loading: false,
  error: false,

  markSetupComplete() {
    set({ setupComplete: true })
  },

  async fetchSetupStatus() {
    if (get().loading) return
    set({ loading: true, error: false })
    try {
      const status = await getSetupStatus()
      set({ setupComplete: !status.needs_setup, loading: false })
    } catch {
      // On error (e.g. network failure), explicitly reset setupComplete
      // to null so the guard sees unknown state and shows error/retry
      set({ setupComplete: null, loading: false, error: true })
    }
  },
}))
