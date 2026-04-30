import { fireEvent, render, screen } from '@testing-library/react'
import { DetailNavBar } from '@/components/ui/detail-nav-bar'

describe('DetailNavBar', () => {
  it('renders nothing when position is null (deep-link / refresh)', () => {
    const { container } = render(
      <DetailNavBar
        canPrev={false}
        canNext={false}
        onPrev={() => {}}
        onNext={() => {}}
        position={null}
      />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('renders position counter and enables both buttons in the middle of a list', () => {
    const onPrev = vi.fn()
    const onNext = vi.fn()
    render(
      <DetailNavBar
        canPrev
        canNext
        onPrev={onPrev}
        onNext={onNext}
        position={{ current: 2, total: 5 }}
        bindShortcuts={false}
      />,
    )
    // Position renders as "{current} of {total}" inside the nav region;
    // assert against the region's text content rather than per-token
    // matches because formatNumber may inject locale-specific spaces.
    const region = screen.getByRole('navigation', { name: /List navigation/i })
    expect(region).toHaveTextContent(/2.*of.*5/)

    fireEvent.click(screen.getByLabelText(/Previous in list/i))
    expect(onPrev).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByLabelText(/Next in list/i))
    expect(onNext).toHaveBeenCalledTimes(1)
  })

  it('disables Previous at the head of the list', () => {
    render(
      <DetailNavBar
        canPrev={false}
        canNext
        onPrev={() => {}}
        onNext={() => {}}
        position={{ current: 1, total: 3 }}
        bindShortcuts={false}
      />,
    )
    expect(screen.getByLabelText(/Previous in list/i)).toBeDisabled()
    expect(screen.getByLabelText(/Next in list/i)).not.toBeDisabled()
  })

  it('binds J / K keyboard shortcuts when bindShortcuts is true', () => {
    const onPrev = vi.fn()
    const onNext = vi.fn()
    render(
      <DetailNavBar
        canPrev
        canNext
        onPrev={onPrev}
        onNext={onNext}
        position={{ current: 2, total: 5 }}
      />,
    )
    fireEvent.keyDown(window, { key: 'j' })
    expect(onPrev).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(window, { key: 'k' })
    expect(onNext).toHaveBeenCalledTimes(1)
  })

  it('does not fire shortcuts when focus is in an input', () => {
    const onPrev = vi.fn()
    const onNext = vi.fn()
    render(
      <div>
        <input data-testid="search" />
        <DetailNavBar
          canPrev
          canNext
          onPrev={onPrev}
          onNext={onNext}
          position={{ current: 2, total: 5 }}
        />
      </div>,
    )
    const input = screen.getByTestId('search')
    input.focus()
    fireEvent.keyDown(input, { key: 'j' })
    fireEvent.keyDown(input, { key: 'k' })
    expect(onPrev).not.toHaveBeenCalled()
    expect(onNext).not.toHaveBeenCalled()
  })

  it('does not bind shortcuts when bindShortcuts=false', () => {
    const onPrev = vi.fn()
    const onNext = vi.fn()
    render(
      <DetailNavBar
        canPrev
        canNext
        onPrev={onPrev}
        onNext={onNext}
        position={{ current: 2, total: 5 }}
        bindShortcuts={false}
      />,
    )
    fireEvent.keyDown(window, { key: 'j' })
    fireEvent.keyDown(window, { key: 'k' })
    expect(onPrev).not.toHaveBeenCalled()
    expect(onNext).not.toHaveBeenCalled()
  })
})
