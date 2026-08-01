import { fireEvent, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, it, expect } from 'vitest'
import { CapabilitiesStep } from '@/pages/setup/CapabilitiesStep'
import { renderWithRouter } from '@/__tests__/test-utils'
import { apiSuccess } from '@/mocks/handlers'
import { buildSettingEntry } from '@/mocks/handlers/settings'
import { server } from '@/test-setup'
import type { SettingNamespace } from '@/api/types/settings'

function chiefOfStaffOn() {
  return http.get('/api/v1/settings/chief_of_staff', () =>
    HttpResponse.json(
      apiSuccess([
        buildSettingEntry({
          value: 'true',
          definition: { namespace: 'chief_of_staff', key: 'explain_chat_enabled' },
        }),
      ]),
    ),
  )
}

function capturePut(): { calls: { namespace: string; key: string; value: string }[] } {
  const calls: { namespace: string; key: string; value: string }[] = []
  server.use(
    http.put('/api/v1/settings/:namespace/:key', async ({ params, request }) => {
      const body = (await request.json()) as { value: string }
      const namespace = String(params['namespace'])
      const key = String(params['key'])
      calls.push({ namespace, key, value: body.value })
      return HttpResponse.json(
        apiSuccess(
          buildSettingEntry({
            value: body.value,
            definition: { namespace: namespace as SettingNamespace, key },
          }),
        ),
      )
    }),
  )
  return { calls }
}

describe('CapabilitiesStep', () => {
  it('renders the on-by-default groups expanded and reflects live values', async () => {
    server.use(chiefOfStaffOn())
    renderWithRouter(<CapabilitiesStep />)
    await waitFor(() =>
      expect(screen.getByRole('switch', { name: 'Explain chat' })).toBeInTheDocument(),
    )
    // Expanded safe groups: their rows are visible.
    expect(screen.getByRole('switch', { name: 'Explain chat' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    expect(screen.getByRole('switch', { name: 'Research' })).toBeInTheDocument()
  })

  it('collapses the advanced groups so their rows are hidden by default', async () => {
    server.use(chiefOfStaffOn())
    renderWithRouter(<CapabilitiesStep />)
    await waitFor(() =>
      expect(screen.getByRole('switch', { name: 'Explain chat' })).toBeInTheDocument(),
    )
    // The advanced group headers are present...
    expect(screen.getByText('Automation')).toBeInTheDocument()
    expect(screen.getByText('Acts on your behalf')).toBeInTheDocument()
    // ...but their rows are collapsed (not rendered until expanded).
    expect(
      screen.queryByRole('switch', { name: 'Self-improvement' }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('switch', { name: 'Direct MCP acting' }),
    ).not.toBeInTheDocument()
  })

  it('persists a toggle through the settings API', async () => {
    server.use(chiefOfStaffOn())
    const { calls } = capturePut()
    renderWithRouter(<CapabilitiesStep />)
    await waitFor(() =>
      expect(screen.getByRole('switch', { name: 'Explain chat' })).toBeInTheDocument(),
    )
    // Starts on (mocked true); toggling persists false.
    fireEvent.click(screen.getByRole('switch', { name: 'Explain chat' }))
    await waitFor(() =>
      expect(calls).toContainEqual({
        namespace: 'chief_of_staff',
        key: 'explain_chat_enabled',
        value: 'false',
      }),
    )
  })

  it('renders the Models section with the per-feature model pickers', async () => {
    server.use(chiefOfStaffOn())
    renderWithRouter(<CapabilitiesStep />)
    // The Models section renders after several settings fetches
    // resolve; the default 1s waitFor flakes under heavy parallel
    // test load (same accommodation as the lazy chart sections).
    await waitFor(
      () => expect(screen.getByLabelText('Coordination model')).toBeInTheDocument(),
      { timeout: 5000 },
    )
    expect(screen.getByLabelText('Embedding model')).toBeInTheDocument()
    expect(screen.getByLabelText('Chief of Staff model')).toBeInTheDocument()
  })

  it('gates the Research model picker on the Research toggle', async () => {
    server.use(chiefOfStaffOn())
    renderWithRouter(<CapabilitiesStep />)
    // Research is on by default, so its model picker is shown.
    await waitFor(() =>
      expect(screen.getByLabelText('Research model')).toBeInTheDocument(),
    )
    // Turning research off hides the picker on the same screen.
    fireEvent.click(screen.getByRole('switch', { name: 'Research' }))
    await waitFor(() =>
      expect(screen.queryByLabelText('Research model')).not.toBeInTheDocument(),
    )
  })
})
