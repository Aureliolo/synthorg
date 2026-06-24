import { create } from 'zustand'
import { getSetupStatus } from '@/api/endpoints/setup'
import { IS_DEV_AUTH_BYPASS } from '@/utils/dev'

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
  setupComplete: IS_DEV_AUTH_BYPASS ? true : null,
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
