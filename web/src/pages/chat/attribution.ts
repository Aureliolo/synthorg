/**
 * A conversational reply is attributed only when both a display name and a
 * role are known. Shared by the unified transcript's turn mapper and its
 * inline event cards so every attributed voice gates identically.
 */
export function hasAttribution(
  name: string | undefined,
  role: string | undefined,
): boolean {
  return Boolean(name && role)
}
