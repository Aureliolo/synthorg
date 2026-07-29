/**
 * The one embedding binding that needs no provider.
 *
 * Mirrors ``BUILTIN_EMBEDDER_PROVIDER`` / ``BUILTIN_EMBEDDER_MODEL`` in
 * ``src/synthorg/memory/embedding/hashing.py``. The backend field is a plain
 * string rather than a literal, so no generated constant exists to import and
 * a rename would otherwise pass type-check while silently switching the
 * warning off. Both surfaces that offer this option read it from here so
 * there is one place to change and one place to check.
 */

export const BUILTIN_EMBEDDER_PROVIDER = 'builtin'
export const BUILTIN_EMBEDDER_MODEL = 'hashing'

/** Option label. Names the trade-off rather than the implementation. */
export const BUILTIN_EMBEDDER_LABEL = 'Built-in (no embedding model)'

/**
 * Shown on whichever control the operator chose it from. This is a
 * consequence of the choice, so it belongs beside the choice and clears
 * itself when the choice changes, rather than living in a banner that
 * outlives it.
 */
export const BUILTIN_EMBEDDER_HINT =
  'Running without an embedding model: recall matches shared vocabulary, not meaning, so agents get literal term overlap instead of related memories. Pick a model here to recall by meaning.'

/** Whether a provider name is the built-in embedder. */
export function isBuiltinEmbedderProvider(provider: string): boolean {
  return provider === BUILTIN_EMBEDDER_PROVIDER
}
