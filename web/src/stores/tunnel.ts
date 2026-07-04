import { create } from 'zustand'
import { updateSetting } from '@/api/endpoints/settings'
import {
  beginTunnelDeviceLogin,
  deleteTunnelCredential,
  getTunnelStatus,
  putTunnelCredential,
  startTunnel as apiStartTunnel,
  stopTunnel as apiStopTunnel,
} from '@/api/endpoints/tunnel'
import type {
  DeviceLoginPrompt,
  TunnelProviderId,
  TunnelProviderStatus,
} from '@/api/types/integrations'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'

const log = createLogger('tunnel-store')

export type TunnelPhase =
  | 'stopped'
  | 'enabling'
  | 'on'
  | 'disabling'
  | 'error'

export interface TunnelState {
  phase: TunnelPhase
  publicUrl: string | null
  error: string | null
  autoStop: boolean
  /** The provider the next start will use; `null` until status loads. */
  selectedProvider: string | null
  /** The provider currently running a tunnel, or `null` when stopped. */
  activeProvider: string | null
  /** Per-provider readiness from the backend snapshot. */
  providers: readonly TunnelProviderStatus[]
  /** Active device-code login prompt (Dev Tunnels), if any. */
  deviceLogin: DeviceLoginPrompt | null
  /**
   * The provider with an in-flight device login (a code minted, waiting on
   * the user to authorise in their browser). Non-null gates Connect off so a
   * pending login is confirmed by polling rather than re-minting a code.
   */
  connectingDevice: string | null
  savingCredential: boolean

  fetchStatus: () => Promise<void>
  start: () => Promise<void>
  stop: () => Promise<void>
  selectProvider: (provider: TunnelProviderId) => Promise<void>
  saveCredential: (provider: string, token: string) => Promise<boolean>
  clearCredential: (provider: string) => Promise<boolean>
  beginDeviceLogin: (provider: string) => Promise<void>
  /** Poll once and resolve a pending login when its provider is configured. */
  pollDeviceLogin: () => Promise<void>
  /** Abandon a pending login (code expired) without minting a new one. */
  cancelDeviceLogin: () => void
  setAutoStop: (enabled: boolean) => void
  reset: () => void
}

const INITIAL_STATE = {
  phase: 'stopped' as const,
  publicUrl: null,
  error: null,
  autoStop: true,
  selectedProvider: null,
  activeProvider: null,
  providers: [] as readonly TunnelProviderStatus[],
  deviceLogin: null,
  connectingDevice: null,
  savingCredential: false,
}

// Module-scoped (escapes Zustand state) on purpose: each operation reads its own generation on entry and bails on completion if a newer operation has incremented past it; stashing it inside the store would make reset() race with in-flight fetches that already captured the old value.
let _operationGeneration = 0

type Set = (partial: Partial<TunnelState>) => void
type Get = () => TunnelState

function toastError(title: string, description: string): void {
  useToastStore.getState().add({ variant: 'error', title, description })
}

function makeLifecycleActions(set: Set, get: Get) {
  return {
    fetchStatus: async () => {
      const gen = ++_operationGeneration
      try {
        const status = await getTunnelStatus()
        if (gen !== _operationGeneration) return
        set({
          publicUrl: status.public_url ?? null,
          phase: status.public_url ? 'on' : 'stopped',
          error: null,
          selectedProvider: status.selected_provider,
          activeProvider: status.active_provider ?? null,
          providers: status.providers,
        })
      } catch (err) {
        if (gen !== _operationGeneration) return
        const message = getErrorMessage(err)
        log.warn('Tunnel status fetch failed:', message)
        set({ phase: 'error', error: message, publicUrl: null })
      }
    },

    start: async () => {
      const gen = ++_operationGeneration
      set({ phase: 'enabling', error: null })
      try {
        const { public_url, provider } = await apiStartTunnel()
        if (gen !== _operationGeneration) return
        set({
          phase: 'on',
          publicUrl: public_url,
          activeProvider: provider,
          error: null,
        })
        useToastStore.getState().add({
          variant: 'success',
          title: 'Tunnel started',
          description: public_url,
        })
      } catch (err) {
        if (gen !== _operationGeneration) return
        const message = getErrorMessage(err)
        log.error('Failed to start tunnel:', message)
        set({ phase: 'error', error: message, publicUrl: null, activeProvider: null })
        toastError('Failed to start tunnel', message)
      }
    },

    stop: async () => {
      const gen = ++_operationGeneration
      set({ phase: 'disabling' })
      try {
        await apiStopTunnel()
        if (gen !== _operationGeneration) return
        set({ phase: 'stopped', publicUrl: null, activeProvider: null, error: null })
        useToastStore.getState().add({ variant: 'info', title: 'Tunnel stopped' })
      } catch (err) {
        if (gen !== _operationGeneration) return
        const message = getErrorMessage(err)
        log.error('Failed to stop tunnel:', message)
        set({ phase: 'error', error: message })
        toastError('Failed to stop tunnel', message)
      }
    },

    selectProvider: async (provider: TunnelProviderId) => {
      const previous = get().selectedProvider
      set({ selectedProvider: provider })
      try {
        await updateSetting('integrations', 'tunnel_provider', { value: provider })
      } catch (err) {
        const message = getErrorMessage(err)
        log.error('Failed to select tunnel provider:', message)
        set({ selectedProvider: previous })
        toastError('Failed to switch tunnel provider', message)
      }
    },
  }
}

function makeCredentialActions(set: Set, get: Get) {
  return {
    saveCredential: async (provider: string, token: string) => {
      set({ savingCredential: true })
      try {
        await putTunnelCredential(provider, token)
        set({ savingCredential: false })
        useToastStore.getState().add({
          variant: 'success',
          title: 'Tunnel credential saved',
        })
        await get().fetchStatus()
        return true
      } catch (err) {
        const message = getErrorMessage(err)
        log.error('Failed to save tunnel credential:', message)
        set({ savingCredential: false })
        toastError('Failed to save credential', message)
        return false
      }
    },

    clearCredential: async (provider: string) => {
      try {
        await deleteTunnelCredential(provider)
        useToastStore.getState().add({
          variant: 'info',
          title: 'Tunnel credential removed',
        })
        await get().fetchStatus()
        return true
      } catch (err) {
        const message = getErrorMessage(err)
        log.error('Failed to remove tunnel credential:', message)
        toastError('Failed to remove credential', message)
        return false
      }
    },

    beginDeviceLogin: async (provider: string) => {
      // Re-minting while a login is already pending for this provider would
      // orphan the outstanding code; the pending state is confirmed by
      // polling instead.
      if (get().connectingDevice === provider) return
      set({ connectingDevice: provider })
      try {
        const prompt = await beginTunnelDeviceLogin(provider)
        // A cancel/reset (or a competing login) during the await supersedes
        // this one; do not resurrect its prompt over the newer state.
        if (get().connectingDevice !== provider) return
        set({ deviceLogin: prompt })
        if (prompt.already_logged_in) {
          set({ connectingDevice: null })
          useToastStore.getState().add({ variant: 'success', title: 'Already signed in' })
          await get().fetchStatus()
        }
      } catch (err) {
        if (get().connectingDevice === provider) set({ connectingDevice: null })
        const message = getErrorMessage(err)
        log.error('Failed to begin device login:', message)
        toastError('Failed to start sign-in', message)
      }
    },

    pollDeviceLogin: async () => {
      const provider = get().connectingDevice
      if (!provider) return
      await get().fetchStatus()
      // A cancel/reset (or the deadline) during the fetch means this poll no
      // longer owns the login; bail so a cancelled sign-in cannot still flip
      // to success and fire a stale "Signed in" toast.
      if (get().connectingDevice !== provider) return
      // fetchStatus refreshed the snapshot; a now-configured provider means
      // the browser-side authorisation completed, so the pending login is
      // done and the code can be discarded.
      const configured = get().providers.find(
        (p) => p.provider_id === provider,
      )?.credential_configured
      if (configured) {
        set({ deviceLogin: null, connectingDevice: null })
        useToastStore.getState().add({ variant: 'success', title: 'Signed in' })
      }
    },

    cancelDeviceLogin: () => {
      if (!get().connectingDevice) return
      set({ deviceLogin: null, connectingDevice: null })
    },
  }
}

export const useTunnelStore = create<TunnelState>()((set, get) => ({
  ...INITIAL_STATE,
  ...makeLifecycleActions(set, get),
  ...makeCredentialActions(set, get),
  setAutoStop: (enabled: boolean) => set({ autoStop: enabled }),
  reset: () => {
    ++_operationGeneration
    set({ ...INITIAL_STATE })
  },
}))
