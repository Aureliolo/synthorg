import type { StoreApi } from 'zustand'
import type {
  SettingDefinition,
  SettingEntry,
  SettingNamespace,
} from '@/api/types/settings'
import type { WsEvent } from '@/api/types/websocket'

/**
 * A guarded-key write the backend rejected pending a confirm + reason (the
 * ``SECURITY_TOGGLE_CONFIRM_REQUIRED`` security-write governance response). The
 * dashboard stages it here so a dialog can collect the operator's reason and
 * retry, instead of dead-ending on a generic error toast.
 */
export interface PendingConfirm {
  ns: SettingNamespace
  key: string
  value: string
}

export interface SettingsState {
  /** ISO 4217 currency code for display formatting. */
  currency: string
  /** Full setting definitions (schema). */
  schema: SettingDefinition[]
  /** All setting entries with resolved values. */
  entries: SettingEntry[]
  /** Whether the initial fetch is in progress. */
  loading: boolean
  /** Error from the most recent fetch. */
  error: string | null
  /**
   * Composite keys ("ns/key") with their in-flight save count.
   *
   * A refcount Map (rather than a Set) so two concurrent saves on the
   * same composite key are tracked independently: when the first one
   * drains, the key stays in the map with count 1, and ``size > 0``
   * still reports "saves in flight" so ``refreshEntries()`` and the
   * post-reset snapshot guard do not race a stale snapshot over the
   * still-pending in-flight result.
   */
  savingKeys: ReadonlyMap<string, number>
  /**
   * Highest applied mutation token per composite key. Used to drop
   * out-of-order ``updateSetting`` responses: if our token is less
   * than the value here, a newer mutation already landed and we must
   * not overwrite ``state.entries`` with stale data. The map is
   * monotonic per key (only ever raised) so an entry never disappears
   * once seen; ``size`` grows with the number of distinct keys
   * mutated in this session, which is bounded by the schema.
   */
  appliedMutationTokens: ReadonlyMap<string, number>
  /**
   * Monotonic counter incremented on every successful save that
   * mutates ``entries``. ``resetSetting`` captures the value before
   * its post-reset refetch and rechecks it after; if a concurrent
   * save completed during the fetch (counter advanced), the
   * refetched snapshot may be stale and is discarded the same way
   * a refetch failure would be.
   */
  entriesGeneration: number
  /** Error from the most recent save attempt. */
  saveError: string | null
  /**
   * A guarded-key write awaiting operator confirm + reason, or ``null``. Set
   * when the backend returns ``SECURITY_TOGGLE_CONFIRM_REQUIRED``; the settings
   * page renders a confirm dialog off this and clears it on confirm / dismiss.
   */
  pendingConfirm: PendingConfirm | null

  /** Fetch the configured currency from the budget settings namespace. */
  fetchCurrency: () => Promise<void>
  /** Fetch both schema and all settings entries. */
  fetchSettingsData: () => Promise<void>
  /** Lightweight re-fetch of entries only (for polling). */
  refreshEntries: () => Promise<void>
  /**
   * Update a single setting value. Returns the updated entry on
   * success, ``null`` on failure (after logging and emitting an
   * error toast).
   */
  updateSetting: (
    ns: SettingNamespace,
    key: string,
    value: string,
  ) => Promise<SettingEntry | null>
  /**
   * Reset a setting to its default value. Returns ``true`` only when
   * the server-side reset succeeds AND the follow-up refetch is
   * applied to ``state.entries``.
   */
  resetSetting: (ns: SettingNamespace, key: string) => Promise<boolean>
  /**
   * Retry the staged :attr:`pendingConfirm` write with ``confirm: true`` and
   * the operator's *reason*. No-op returning ``null`` when nothing is staged.
   */
  confirmPendingUpdate: (reason: string) => Promise<SettingEntry | null>
  /** Discard the staged guarded-key write without applying it. */
  dismissPendingConfirm: () => void
  /** Handle a WebSocket event on the system channel. */
  updateFromWsEvent: (event: WsEvent) => void
}

export type SettingsSet = StoreApi<SettingsState>['setState']
export type SettingsGet = StoreApi<SettingsState>['getState']
