import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ToastItem } from '@/stores/toast'
import { useToastStore } from '@/stores/toast'
import { Toast, ToastContainer } from '@/components/ui/toast'

// Mock motion/react

vi.mock('motion/react', async () => {
  const actual = await vi.importActual<typeof import('motion/react')>('motion/react')
  return {
    ...actual,
    AnimatePresence: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    motion: {
      ...actual.motion,
      div: ({
        children,
        className,
        role,
        'aria-live': ariaLive,
        ...rest
      }: React.HTMLAttributes<HTMLDivElement>) => (
        <div className={className} role={role} aria-live={ariaLive} {...rest}>
          {children}
        </div>
      ),
    },
  }
})


const baseToast: ToastItem = {
  id: '1',
  variant: 'success',
  title: 'Saved successfully',
}

describe('Toast', () => {
  it('renders title', () => {
    render(<Toast toast={baseToast} onDismiss={vi.fn()} />)
    expect(screen.getByText('Saved successfully')).toBeInTheDocument()
  })

  it('renders description when provided', () => {
    render(
      <Toast
        toast={{ ...baseToast, description: 'Changes have been saved.' }}
        onDismiss={vi.fn()}
      />,
    )
    expect(screen.getByText('Changes have been saved.')).toBeInTheDocument()
  })

  it('close button calls onDismiss', async () => {
    const user = userEvent.setup()
    const onDismiss = vi.fn()
    render(<Toast toast={baseToast} onDismiss={onDismiss} />)

    await user.click(screen.getByRole('button', { name: /dismiss/i }))
    expect(onDismiss).toHaveBeenCalledWith('1')
  })

  it('error variant has aria-live="assertive"', () => {
    const { container } = render(
      <Toast toast={{ ...baseToast, variant: 'error' }} onDismiss={vi.fn()} />,
    )
    const alert = container.querySelector('[aria-live]')
    expect(alert).toHaveAttribute('aria-live', 'assertive')
  })

  it.each(['success', 'warning', 'info'] as const)(
    '%s variant has aria-live="polite"',
    (variant) => {
      const { container } = render(
        <Toast toast={{ ...baseToast, variant }} onDismiss={vi.fn()} />,
      )
      const alert = container.querySelector('[aria-live]')
      expect(alert).toHaveAttribute('aria-live', 'polite')
    },
  )

  it('error variant has role="alert"', () => {
    render(<Toast toast={{ ...baseToast, variant: 'error' }} onDismiss={vi.fn()} />)
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  it('non-error variants have role="status"', () => {
    render(<Toast toast={baseToast} onDismiss={vi.fn()} />)
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('hides dismiss button when dismissible is false', () => {
    render(
      <Toast
        toast={{ ...baseToast, dismissible: false }}
        onDismiss={vi.fn()}
      />,
    )
    expect(screen.queryByRole('button', { name: /dismiss/i })).not.toBeInTheDocument()
  })
})

describe('ToastContainer', () => {
  beforeEach(() => {
    useToastStore.getState().dismissAll()
  })

  it('renders toasts from the store', () => {
    useToastStore.getState().add({ variant: 'info', title: 'Hello' })
    render(<ToastContainer />)
    expect(screen.getByText('Hello')).toBeInTheDocument()
  })

  it('limits visible toasts to maxVisible', () => {
    for (let i = 0; i < 5; i++) {
      useToastStore.getState().add({ variant: 'info', title: `Toast ${i}` })
    }
    render(<ToastContainer maxVisible={3} />)
    const alerts = screen.getAllByRole('status')
    expect(alerts).toHaveLength(3)
  })

  it('defaults maxVisible to 3', () => {
    for (let i = 0; i < 5; i++) {
      useToastStore.getState().add({ variant: 'info', title: `Toast ${i}` })
    }
    render(<ToastContainer />)
    const alerts = screen.getAllByRole('status')
    expect(alerts).toHaveLength(3)
  })
})
