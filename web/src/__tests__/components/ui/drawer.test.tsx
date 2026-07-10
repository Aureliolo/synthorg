import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Drawer } from '@/components/ui/drawer'

// Test wrapper for focus-restore testing (needs state to toggle open/close)
function FocusRestoreWrapper() {
  const [open, setOpen] = React.useState(false)
  return (
    <>
      <button type="button" data-testid="opener" onClick={() => setOpen(true)}>Open</button>
      <Drawer open={open} onClose={() => setOpen(false)} title="Test">Content</Drawer>
    </>
  )
}

describe('Drawer', () => {
  it('renders nothing when closed', () => {
    render(<Drawer open={false} onClose={() => {}} title="Test">Content</Drawer>)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('renders dialog when open', () => {
    render(<Drawer open={true} onClose={() => {}} title="Compare">Content</Drawer>)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('renders title', () => {
    render(<Drawer open={true} onClose={() => {}} title="Compare Templates">Content</Drawer>)
    expect(screen.getByText('Compare Templates')).toBeInTheDocument()
  })

  it('renders children', () => {
    render(
      <Drawer open={true} onClose={() => {}} title="Test">
        <p>Drawer content</p>
      </Drawer>,
    )
    expect(screen.getByText('Drawer content')).toBeInTheDocument()
  })

  it('has accessible name matching title', () => {
    render(<Drawer open={true} onClose={() => {}} title="Compare">Content</Drawer>)
    expect(screen.getByRole('dialog', { name: 'Compare' })).toBeInTheDocument()
  })

  it('calls onClose when close button is clicked', async () => {
    const handleClose = vi.fn()
    const user = userEvent.setup()
    render(<Drawer open={true} onClose={handleClose} title="Test">Content</Drawer>)
    await user.click(screen.getByLabelText('Close'))
    expect(handleClose).toHaveBeenCalledOnce()
  })

  it('calls onClose when overlay is clicked', async () => {
    const handleClose = vi.fn()
    const user = userEvent.setup()
    render(<Drawer open={true} onClose={handleClose} title="Test">Content</Drawer>)
    await user.click(screen.getByTestId('drawer-overlay'))
    expect(handleClose).toHaveBeenCalledOnce()
  })

  it('calls onClose on Escape key', async () => {
    const handleClose = vi.fn()
    const user = userEvent.setup()
    render(<Drawer open={true} onClose={handleClose} title="Test">Content</Drawer>)
    await user.keyboard('{Escape}')
    expect(handleClose).toHaveBeenCalledOnce()
  })

  it('restores focus to the opener element when closed', async () => {
    const user = userEvent.setup()
    render(<FocusRestoreWrapper />)
    const opener = screen.getByTestId('opener')

    // Open the drawer
    await user.click(opener)

    // Drawer should be open
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    // Close the drawer by clicking the close button
    await user.click(screen.getByLabelText('Close'))

    // Focus should be restored to the opener
    expect(document.activeElement).toBe(opener)
  })

  describe('side prop', () => {
    it('renders on the left when side="left"', () => {
      render(<Drawer open={true} onClose={() => {}} title="Left" side="left">Content</Drawer>)
      const dialog = screen.getByRole('dialog')
      expect(dialog.className).toMatch(/left-0/)
      expect(dialog.className).not.toMatch(/right-0/)
    })

    it('renders on the right by default', () => {
      render(<Drawer open={true} onClose={() => {}} title="Right">Content</Drawer>)
      const dialog = screen.getByRole('dialog')
      expect(dialog.className).toMatch(/right-0/)
      expect(dialog.className).not.toMatch(/left-0/)
    })
  })

  describe('optional title (headerless mode)', () => {
    it('does not render header when title is omitted', () => {
      render(<Drawer open={true} onClose={() => {}} ariaLabel="Custom panel">Content</Drawer>)
      expect(screen.queryByLabelText('Close')).not.toBeInTheDocument()
      expect(screen.queryByRole('heading')).not.toBeInTheDocument()
    })

    it('uses ariaLabel for accessible name when title is omitted', () => {
      render(<Drawer open={true} onClose={() => {}} ariaLabel="Navigation menu">Content</Drawer>)
      expect(screen.getByRole('dialog', { name: 'Navigation menu' })).toBeInTheDocument()
    })

    it('ariaLabel takes precedence over title when both are provided', () => {
      render(<Drawer open={true} onClose={() => {}} title="Visible Title" ariaLabel="Screen Reader Label">Content</Drawer>)
      expect(screen.getByRole('dialog')).toHaveAttribute('aria-label', 'Screen Reader Label')
    })

    it('renders plain heading when ariaLabel overrides title', () => {
      render(<Drawer open={true} onClose={() => {}} title="Visible" ariaLabel="Override">Content</Drawer>)
      // Should render a plain <h2>, not BaseDrawer.Title (which would add aria-labelledby)
      const heading = screen.getByRole('heading', { name: 'Visible' })
      expect(heading.tagName).toBe('H2')
    })

    it('renders BaseDrawer.Title when only title is given', () => {
      render(<Drawer open={true} onClose={() => {}} title="Only Title">Content</Drawer>)
      // BaseDrawer.Title provides the accessible name via aria-labelledby
      expect(screen.getByRole('dialog', { name: 'Only Title' })).toBeInTheDocument()
    })
  })

  describe('header and footer', () => {
    it('renders custom header content in place of the built-in title', () => {
      render(
        <Drawer open={true} onClose={() => {}} ariaLabel="Review" header={<span>Custom Header Badge</span>}>
          Content
        </Drawer>,
      )
      expect(screen.getByText('Custom Header Badge')).toBeInTheDocument()
      // The close button is still supplied inside the custom header row.
      expect(screen.getByLabelText('Close')).toBeInTheDocument()
    })

    it('keeps the accessible name from ariaLabel when a header is set', () => {
      render(
        <Drawer open={true} onClose={() => {}} ariaLabel="Review approval" header={<span>H</span>}>
          Content
        </Drawer>,
      )
      expect(screen.getByRole('dialog', { name: 'Review approval' })).toBeInTheDocument()
    })

    it('renders footer content in the pinned footer region', () => {
      render(
        <Drawer open={true} onClose={() => {}} title="Review" footer={<button type="button">Acknowledge</button>}>
          Content
        </Drawer>,
      )
      const footer = screen.getByTestId('drawer-footer')
      expect(footer).toContainElement(screen.getByRole('button', { name: 'Acknowledge' }))
    })
  })

  describe('className prop', () => {
    it('merges className into the popup element', () => {
      render(<Drawer open={true} onClose={() => {}} title="Test" className="custom-class">Content</Drawer>)
      const dialog = screen.getByRole('dialog')
      expect(dialog.className).toMatch(/custom-class/)
    })
  })

  describe('contentClassName', () => {
    it('merges contentClassName into the content wrapper', () => {
      render(
        <Drawer open={true} onClose={() => {}} title="Test" contentClassName="p-0">
          <span>Hello</span>
        </Drawer>,
      )
      const content = screen.getByTestId('drawer-content')
      expect(content.className).toMatch(/p-0/)
    })
  })

  describe('width prop', () => {
    it('defaults to the "default" variant when width is omitted', () => {
      render(<Drawer open={true} onClose={() => {}} title="Test">Content</Drawer>)
      const popup = screen.getByRole('dialog')
      expect(popup.className).toMatch(/w-\[var\(--so-drawer-width-default\)\]/)
    })

    it('applies the narrow width token when width="narrow"', () => {
      render(
        <Drawer open={true} onClose={() => {}} title="Test" width="narrow">
          Content
        </Drawer>,
      )
      const popup = screen.getByRole('dialog')
      expect(popup.className).toMatch(/w-\[var\(--so-drawer-width-narrow\)\]/)
    })

    it('applies the wide width token when width="wide"', () => {
      render(
        <Drawer open={true} onClose={() => {}} title="Test" width="wide">
          Content
        </Drawer>,
      )
      const popup = screen.getByRole('dialog')
      expect(popup.className).toMatch(/w-\[var\(--so-drawer-width-wide\)\]/)
    })
  })
})
