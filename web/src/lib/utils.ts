import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Standard focus indicator classes per design spec:
 * 2px solid ring, 2px offset, accent color, :focus-visible only.
 */
export const FOCUS_RING =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-background'

/**
 * Merge a caller-supplied ARIA id token list with a component-managed
 * token, preserving both. Prevents a caller-supplied `aria-describedby`
 * from silently dropping the component's own hint/error ids so screen
 * readers continue to receive the validation text.
 *
 * For IDREFS attributes only (`aria-describedby`, `aria-labelledby`,
 * `aria-owns`). An IDREF attribute such as `aria-errormessage` takes one
 * id, and a list there resolves to nothing at all.
 */
export function mergeAriaToken(
  incoming: string | undefined,
  managed: string | undefined,
): string | undefined {
  const tokens = new Set<string>()
  if (incoming) {
    for (const token of incoming.split(/\s+/)) {
      if (token) tokens.add(token)
    }
  }
  if (managed) tokens.add(managed)
  if (tokens.size === 0) return undefined
  return [...tokens].join(' ')
}
