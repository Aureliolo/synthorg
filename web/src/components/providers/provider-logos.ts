/**
 * Preset names that have a bundled SVG in `web/public/provider-logos/`.
 *
 * Kept as a static set so the picker can decide between the brand mark
 * and the fallback `Server` icon synchronously, without an HTTP probe
 * or onError event listener (which doesn't fire reliably on
 * `mask-image`). Add a new entry here whenever you drop a new SVG into
 * the public directory.
 */
export const KNOWN_LOGOS: ReadonlySet<string> = new Set([
  'anthropic',
  'azure',
  'cerebras',
  'cohere',
  'deepseek',
  'fireworks_ai',
  'gemini',
  'groq',
  'lm-studio',
  'mistral',
  'moonshot',
  'nvidia_nim',
  'ollama',
  'ollama-cloud',
  'openai',
  'openrouter',
  'sambanova',
  'together_ai',
  'vllm',
  'xai',
])

/**
 * Pick the best brand-logo key from a set of candidate names.
 *
 * A configured provider exposes several identifiers (its operator-chosen
 * name, its litellm provider, its driver); the first one with a bundled
 * SVG wins so e.g. `ollama-cloud` resolves to its own mark rather than
 * the `openai` driver behind it. Returns the first candidate verbatim
 * when none is known, letting the logo component fall back to the icon.
 */
export function providerLogoName(
  ...candidates: readonly (string | null | undefined)[]
): string {
  const named = candidates.filter((c): c is string => Boolean(c))
  return named.find((c) => KNOWN_LOGOS.has(c)) ?? named[0] ?? ''
}
