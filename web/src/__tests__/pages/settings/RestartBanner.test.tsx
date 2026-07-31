import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { RestartBanner } from '@/pages/settings/RestartBanner'
import type { PendingRestartSetting } from '@/api/types/system'
import { server } from '@/test-setup'

const API = '/api/v1/meta/restart'

function pendingSetting(key: string): PendingRestartSetting {
  return {
    namespace: 'memory',
    key,
    description: `What ${key} does`,
    updated_at: '2026-07-31T09:00:00Z',
  }
}

function seedStatus(pending: PendingRestartSetting[], supervised: boolean) {
  server.use(
    http.get(API, () =>
      HttpResponse.json({ success: true, data: { pending, supervised } }),
    ),
  )
}

describe('RestartBanner', () => {
  it('renders nothing when the backend reports no pending settings', async () => {
    seedStatus([], true)
    const { container } = render(<RestartBanner />)
    // The banner hydrates on mount, so the empty answer has to be the settled
    // state rather than merely the initial one.
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(container.firstChild).toBeNull()
  })

  it('renders the singular message for one pending setting', async () => {
    seedStatus([pendingSetting('embedder_model')], true)
    render(<RestartBanner />)
    expect(
      await screen.findByText(/1 setting requires a restart/i),
    ).toBeInTheDocument()
  })

  it('renders the plural message for several pending settings', async () => {
    seedStatus(
      [pendingSetting('embedder_model'), pendingSetting('embedder_dims')],
      true,
    )
    render(<RestartBanner />)
    expect(
      await screen.findByText(/2 settings require a restart/i),
    ).toBeInTheDocument()
  })

  it('offers the restart control only where the process is supervised', async () => {
    seedStatus([pendingSetting('embedder_model')], true)
    render(<RestartBanner />)
    expect(
      await screen.findByRole('button', { name: /restart now/i }),
    ).toBeInTheDocument()
  })

  it('explains rather than offering a button when unsupervised', async () => {
    seedStatus([pendingSetting('embedder_model')], false)
    render(<RestartBanner />)
    expect(await screen.findByText(/not supervised/i)).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /restart now/i }),
    ).not.toBeInTheDocument()
  })

  it('says so when the status cannot be read, rather than showing nothing', async () => {
    server.use(
      http.get(API, () =>
        HttpResponse.json(
          { success: false, error: 'Settings service unavailable' },
          { status: 503 },
        ),
      ),
    )
    render(<RestartBanner />)
    expect(
      await screen.findByText(/could not check whether a restart is needed/i),
    ).toBeInTheDocument()
  })

  it('has alert role for accessibility', async () => {
    seedStatus([pendingSetting('embedder_model')], true)
    render(<RestartBanner />)
    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})
