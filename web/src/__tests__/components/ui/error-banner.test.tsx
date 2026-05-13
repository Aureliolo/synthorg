import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import fc from 'fast-check'
import { ErrorBanner } from '@/components/ui/error-banner'

describe('ErrorBanner', () => {
  it('renders title and description', () => {
    render(<ErrorBanner title="Failed" description="Retry?" />)
    expect(screen.getByText('Failed')).toBeInTheDocument()
    expect(screen.getByText('Retry?')).toBeInTheDocument()
  })

  it('error severity uses role=alert and aria-live=assertive', () => {
    render(<ErrorBanner severity="error" title="Boom" />)
    const banner = screen.getByRole('alert')
    expect(banner).toHaveAttribute('aria-live', 'assertive')
  })

  it('warning severity uses role=status and aria-live=polite', () => {
    render(<ErrorBanner severity="warning" title="Hmm" />)
    const banner = screen.getByRole('status')
    expect(banner).toHaveAttribute('aria-live', 'polite')
  })

  it('info severity uses role=status and aria-live=polite', () => {
    render(<ErrorBanner severity="info" title="FYI" />)
    const banner = screen.getByRole('status')
    expect(banner).toHaveAttribute('aria-live', 'polite')
  })

  it('offline variant forces warning semantics and renders WifiOff icon', () => {
    const { container } = render(<ErrorBanner variant="offline" title="Offline" />)
    const banner = screen.getByRole('status')
    expect(banner).toHaveAttribute('aria-live', 'polite')
    // WifiOff is a lucide svg; no explicit assert on class name, just that svg renders
    expect(container.querySelector('svg')).toBeInTheDocument()
  })

  it('onRetry renders Retry button and fires on click', async () => {
    const user = userEvent.setup()
    const onRetry = vi.fn()
    render(<ErrorBanner title="Failed" onRetry={onRetry} />)
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('onDismiss renders Dismiss button and fires on click', async () => {
    const user = userEvent.setup()
    const onDismiss = vi.fn()
    render(<ErrorBanner title="Failed" onDismiss={onDismiss} />)
    await user.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  it('action prop renders custom action button', async () => {
    const user = userEvent.setup()
    const onClick = vi.fn()
    render(
      <ErrorBanner
        title="Update available"
        action={{ label: 'Reload', onClick }}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Reload' }))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('inline variant applies compact density classes', () => {
    const { container } = render(<ErrorBanner variant="inline" title="Compact" />)
    // Uses the density-aware `p-card` token (same as the `section` variant)
    // but keeps the tighter `text-xs` + `gap-2` for compact contexts.
    expect(container.firstChild).toHaveClass('p-card', 'gap-2', 'text-xs')
  })

  it('no Retry button when onRetry is absent', () => {
    render(<ErrorBanner title="Failed" />)
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
  })

  it('applies className', () => {
    const { container } = render(<ErrorBanner title="Test" className="custom-class" />)
    expect(container.firstChild).toHaveClass('custom-class')
  })

  it('property: severity/variant map deterministically to role + aria-live', () => {
    fc.assert(
      fc.property(
        fc.constantFrom('error', 'warning', 'info'),
        fc.constantFrom('inline', 'section', 'offline'),
        (severity, variant) => {
          const { unmount } = render(
            <ErrorBanner
              severity={severity as 'error' | 'warning' | 'info'}
              variant={variant as 'inline' | 'section' | 'offline'}
              title="Prop-test"
            />,
          )
          // Offline always forces warning semantics regardless of severity.
          const expectError = variant !== 'offline' && severity === 'error'
          const expectedRole = expectError ? 'alert' : 'status'
          const expectedLive = expectError ? 'assertive' : 'polite'
          const banner = screen.getByRole(expectedRole)
          expect(banner).toHaveAttribute('aria-live', expectedLive)
          unmount()
        },
      ),
      { numRuns: 20 },
    )
  })

  describe('correlationId chip', () => {
    it('renders a copy button when correlationId is provided', () => {
      render(<ErrorBanner title="Boom" correlationId="abc-12345678" />)
      expect(
        screen.getByRole('button', { name: /Copy correlation ID abc-12345678/ }),
      ).toBeInTheDocument()
    })

    it('shows a truncated form for long IDs', () => {
      render(
        <ErrorBanner
          title="Boom"
          correlationId="01J7CK5D9SXKZ0HFAQK4E8RBQX"
        />,
      )
      // The button label contains the full ID for screen readers, the
      // visible text is truncated to keep the chip compact.
      const button = screen.getByRole('button', {
        name: /01J7CK5D9SXKZ0HFAQK4E8RBQX/,
      })
      const visibleText = button.textContent ?? ''
      expect(visibleText.length).toBeLessThan('01J7CK5D9SXKZ0HFAQK4E8RBQX'.length)
    })

    it('copies the full correlation ID to the clipboard', async () => {
      const user = userEvent.setup()
      const writeText = vi.fn().mockResolvedValue(undefined)
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: { writeText },
      })
      render(<ErrorBanner title="Boom" correlationId="ulid-12345" />)
      await user.click(
        screen.getByRole('button', { name: /Copy correlation ID ulid-12345/ }),
      )
      expect(writeText).toHaveBeenCalledWith('ulid-12345')
    })

    it('does not render when correlationId is null', () => {
      render(<ErrorBanner title="Boom" correlationId={null} />)
      expect(
        screen.queryByRole('button', { name: /Copy correlation ID/ }),
      ).not.toBeInTheDocument()
    })
  })
})
