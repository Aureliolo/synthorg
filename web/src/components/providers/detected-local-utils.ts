/** Shared lookup and types for the DetectedLocalList package. */

/**
 * Local preset name -> cloud-counterpart preset name.
 *
 * When a detected local preset has an entry in this map, its row
 * renders an additional `[Add cloud]` button that opens the
 * credential form pre-filled with the hosted variant. Today this
 * is `ollama` -> `ollama-cloud`; add more entries here when other
 * local backends gain cloud counterparts (e.g. one day
 * `'lm-studio': 'lm-studio-cloud'`). Keep both preset names valid
 * entries in `PROVIDER_PRESETS` on the backend.
 */
export const LOCAL_TO_CLOUD_COUNTERPART: Readonly<Record<string, string>> = {
  ollama: 'ollama-cloud',
}

export type AddingKind = 'local' | 'cloud'
