/**
 * Lower-cased `key` for a keyboard event, or `''` when the event carries none.
 *
 * The DOM types declare `KeyboardEvent.key` as `string`, but events synthesised
 * by the platform and by third-party libraries (Chrome autofill on a password
 * field, for one) arrive without it, so an unguarded `.toLowerCase()` in a
 * document-level listener throws past the compiler into the global error
 * handler. Taking an optional `key` keeps that guard honest rather than a
 * condition the type-checker reports as always-true.
 *
 * Lower-casing also makes a letter shortcut survive Caps Lock and the AZERTY
 * layouts that report `'K'` for the same physical keystroke.
 */
export function normalisedKey(event: { readonly key?: string }): string {
  return event.key?.toLowerCase() ?? ''
}
