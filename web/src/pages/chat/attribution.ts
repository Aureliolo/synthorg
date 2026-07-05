/**
 * A conversational reply is attributed only when both a display name and a
 * role are known. Shared across the propose, group, and direct-action
 * surfaces so every mode gates the {@link ResponderAttribution} identically.
 */
export function hasAttribution(
  name: string | undefined,
  role: string | undefined,
): boolean {
  return Boolean(name && role)
}
