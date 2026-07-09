import { renderHook } from '@testing-library/react'
import { useProviderLocality } from '@/hooks/useProviderLocality'
import type { ProviderConfig } from '@/api/types/providers'

function provider(baseUrl: string | null): ProviderConfig {
  // Only base_url is read; cast through unknown rather than synthesising every
  // credential-indicator field of the full ProviderConfig.
  return { base_url: baseUrl, models: [] } as unknown as ProviderConfig
}

describe('useProviderLocality', () => {
  it('maps each provider to its locality', () => {
    const { result } = renderHook(() =>
      useProviderLocality({
        local: provider('http://localhost:11434'),
        remote: provider('https://api.example.com/v1'),
      }),
    )
    expect(result.current).toEqual({ local: true, remote: false })
  })

  it('returns a stable reference when providers is unchanged', () => {
    const providers = { local: provider('http://127.0.0.1:8080') }
    const { result, rerender } = renderHook(() => useProviderLocality(providers))
    const first = result.current
    rerender()
    expect(result.current).toBe(first)
  })
})
