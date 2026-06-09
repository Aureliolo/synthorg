import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'

import { SecurityConfigSection } from '@/pages/settings/SecurityConfigSection'
import { useToastStore } from '@/stores/toast'
import { downloadTextFile } from '@/utils/download'
import { server } from '@/test-setup'

vi.mock('@/utils/download', () => ({
  downloadTextFile: vi.fn(),
}))

const downloadMock = vi.mocked(downloadTextFile)

function toastTitles(): string[] {
  return useToastStore.getState().toasts.map((t) => t.title)
}

async function uploadConfig(config: unknown): Promise<void> {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]')
  if (input === null) throw new Error('file input not rendered')
  const file = new File([JSON.stringify(config)], 'security-config.json', {
    type: 'application/json',
  })
  await userEvent.upload(input, file)
}

describe('SecurityConfigSection', () => {
  beforeEach(() => {
    downloadMock.mockClear()
    useToastStore.getState().dismissAll()
  })

  it('exports the config as a downloaded JSON file', async () => {
    const user = userEvent.setup()
    render(<SecurityConfigSection />)

    await user.click(screen.getByRole('button', { name: 'Export' }))

    await waitFor(() => {
      expect(downloadMock).toHaveBeenCalledTimes(1)
    })
    const [content, filename, mime] = downloadMock.mock.calls[0] ?? ['', '', '']
    expect(filename).toBe('security-config.json')
    expect(mime).toBe('application/json')
    expect(JSON.parse(content) as unknown).toEqual({ enabled: true, audit_enabled: true })
    expect(toastTitles()).toContain('Security configuration exported')
  })

  it('surfaces an error toast when export fails', async () => {
    server.use(
      http.get('/api/v1/settings/security/export', () =>
        HttpResponse.json({ success: false }, { status: 500 }),
      ),
    )
    const user = userEvent.setup()
    render(<SecurityConfigSection />)

    await user.click(screen.getByRole('button', { name: 'Export' }))

    await waitFor(() => {
      expect(toastTitles()).toContain('Export failed')
    })
    expect(downloadMock).not.toHaveBeenCalled()
  })

  it('imports a config after confirmation', async () => {
    const user = userEvent.setup()
    render(<SecurityConfigSection />)

    await uploadConfig({ enabled: false })

    const confirm = await screen.findByRole('button', { name: 'Import' })
    await user.click(confirm)

    await waitFor(() => {
      expect(toastTitles()).toContain('Security configuration imported')
    })
  })

  it('does not import when the confirmation is cancelled', async () => {
    const user = userEvent.setup()
    render(<SecurityConfigSection />)

    await uploadConfig({ enabled: false })

    await user.click(await screen.findByRole('button', { name: 'Cancel' }))

    expect(toastTitles()).not.toContain('Security configuration imported')
  })

  it('rejects a file that is not a JSON object', async () => {
    render(<SecurityConfigSection />)

    await uploadConfig([1, 2, 3])

    await waitFor(() => {
      expect(toastTitles()).toContain('Could not read import file')
    })
    expect(screen.queryByRole('button', { name: 'Import' })).not.toBeInTheDocument()
  })
})
