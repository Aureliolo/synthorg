/**
 * Classify a provider base URL as locally-hosted (loopback / private / a
 * localhost alias). Mirrors the backend `core.url_locality.is_local_url` so the
 * setup roster can flag which agents run on a free local backend versus a paid
 * remote. Presentation only: derived from provider data already fetched, never
 * persisted.
 */

const LOCALHOST_ALIASES: ReadonlySet<string> = new Set([
  'localhost',
  '127.0.0.1',
  '0.0.0.0',
  'host.docker.internal',
  '172.17.0.1',
  '::1',
])

// Private / loopback / link-local IPv4 ranges as inclusive 32-bit bounds.
const PRIVATE_V4_RANGES: ReadonlyArray<readonly [number, number]> = [
  [0x0a000000, 0x0affffff], // 10.0.0.0/8 (private)
  [0x7f000000, 0x7fffffff], // 127.0.0.0/8 (loopback)
  [0xc0a80000, 0xc0a8ffff], // 192.168.0.0/16 (private)
  [0xac100000, 0xac1fffff], // 172.16.0.0/12 (private)
  [0xa9fe0000, 0xa9feffff], // 169.254.0.0/16 (link-local)
]

function ipv4ToInt(host: string): number | null {
  const octets = host.split('.').map(Number)
  if (octets.length !== 4) return null
  if (octets.some((n) => !Number.isInteger(n) || n < 0 || n > 255)) return null
  return octets.reduce((acc, n) => acc * 256 + n, 0)
}

function isPrivateIpv4(host: string): boolean {
  const value = ipv4ToInt(host)
  if (value === null) return false
  return PRIVATE_V4_RANGES.some(([lo, hi]) => value >= lo && value <= hi)
}

function isLocalIpv6(host: string): boolean {
  // ::1 loopback, fe80::/10 link-local, fc00::/7 unique-local.
  return (
    host === '::1' ||
    host.startsWith('fe80:') ||
    host.startsWith('fc') ||
    host.startsWith('fd')
  )
}

function parseHost(url: string): string | null {
  try {
    return new URL(url).hostname.replace(/\.$/, '').toLowerCase()
  } catch {
    return null
  }
}

/** True when *url* targets a local/self-hosted backend, false otherwise. */
export function isLocalUrl(url: string | null | undefined): boolean {
  if (!url) return false
  const host = parseHost(url)
  if (host === null) return false
  // `new URL` wraps an IPv6 host in brackets; strip them before matching.
  const bare = host.startsWith('[') ? host.slice(1, -1) : host
  return LOCALHOST_ALIASES.has(bare) || isLocalIpv6(bare) || isPrivateIpv4(bare)
}
