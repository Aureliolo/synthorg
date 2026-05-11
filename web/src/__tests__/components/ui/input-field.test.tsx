import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { InputField, PasswordVisibilityGroup } from '@/components/ui/input-field'

describe('InputField', () => {
  it('renders label text', () => {
    render(<InputField label="Company Name" />)
    expect(screen.getByLabelText('Company Name')).toBeInTheDocument()
  })

  it('renders required indicator when required', () => {
    render(<InputField label="Name" required />)
    expect(screen.getByText('*')).toBeInTheDocument()
  })

  it('does not render required indicator when not required', () => {
    render(<InputField label="Name" />)
    expect(screen.queryByText('*')).not.toBeInTheDocument()
  })

  it('renders error message', () => {
    render(<InputField label="Name" error="Name is required" />)
    expect(screen.getByRole('alert')).toHaveTextContent('Name is required')
  })

  it('sets aria-invalid when error is present', () => {
    render(<InputField label="Name" error="Required" />)
    expect(screen.getByLabelText('Name')).toHaveAttribute('aria-invalid', 'true')
  })

  it('does not set aria-invalid when no error', () => {
    render(<InputField label="Name" />)
    expect(screen.getByLabelText('Name')).toHaveAttribute('aria-invalid', 'false')
  })

  it('renders hint text when no error', () => {
    render(<InputField label="Name" hint="Max 200 characters" />)
    expect(screen.getByText('Max 200 characters')).toBeInTheDocument()
  })

  it('hides hint when error is present', () => {
    render(<InputField label="Name" hint="Max 200 chars" error="Required" />)
    expect(screen.queryByText('Max 200 chars')).not.toBeInTheDocument()
    expect(screen.getByText('Required')).toBeInTheDocument()
  })

  it('renders as textarea when multiline', () => {
    render(<InputField label="Description" multiline rows={4} />)
    const textarea = screen.getByLabelText('Description')
    expect(textarea.tagName).toBe('TEXTAREA')
  })

  it('handles user input', async () => {
    const user = userEvent.setup()
    render(<InputField label="Name" />)
    const input = screen.getByLabelText('Name')
    await user.type(input, 'Acme Corp')
    expect(input).toHaveValue('Acme Corp')
  })

  it('respects disabled state', () => {
    render(<InputField label="Name" disabled />)
    expect(screen.getByLabelText('Name')).toBeDisabled()
  })

  it('renders a leading icon and adds left padding to the input', () => {
    render(
      <InputField
        label="Search"
        leadingIcon={<svg data-testid="lead-icon" aria-hidden="true" />}
      />,
    )
    expect(screen.getByTestId('lead-icon')).toBeInTheDocument()
    expect(screen.getByLabelText('Search')).toHaveClass('pl-8')
  })

  it('renders a trailing element and adds right padding to the input', async () => {
    const user = userEvent.setup()
    const onClick = vi.fn()
    render(
      <InputField
        label="Search"
        trailingElement={
          <button type="button" aria-label="Clear" onClick={onClick}>
            x
          </button>
        }
      />,
    )
    const button = screen.getByRole('button', { name: 'Clear' })
    expect(button).toBeInTheDocument()
    expect(screen.getByLabelText('Search')).toHaveClass('pr-8')
    await user.click(button)
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('does not leak the leadingIcon / trailingElement props onto the DOM input', () => {
    render(
      <InputField
        label="Search"
        leadingIcon={<svg data-testid="lead" aria-hidden="true" />}
        trailingElement={<span data-testid="trail">x</span>}
      />,
    )
    const input = screen.getByLabelText('Search')
    expect(input).not.toHaveAttribute('leadingicon')
    expect(input).not.toHaveAttribute('trailingelement')
  })

  it('keeps leadingIcon / trailingElement padding off the input when not provided', () => {
    render(<InputField label="Plain" />)
    const input = screen.getByLabelText('Plain')
    expect(input).not.toHaveClass('pl-8')
    expect(input).not.toHaveClass('pr-8')
  })

  describe('password visibility toggle', () => {
    it('renders an eye toggle button when type="password"', () => {
      render(<InputField label="Password" type="password" />)
      const button = screen.getByRole('button', { name: 'Show password' })
      expect(button).toBeInTheDocument()
      expect(button).toHaveAttribute('aria-pressed', 'false')
      expect(button).toHaveAttribute('type', 'button')
    })

    it('starts hidden (type="password" on the input)', () => {
      render(<InputField label="Password" type="password" />)
      expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password')
    })

    it('reveals the value when the toggle is clicked', async () => {
      const user = userEvent.setup()
      render(<InputField label="Password" type="password" />)
      await user.click(screen.getByRole('button', { name: 'Show password' }))
      expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'text')
      const button = screen.getByRole('button', { name: 'Hide password' })
      expect(button).toHaveAttribute('aria-pressed', 'true')
    })

    it('hides the value when toggled twice', async () => {
      const user = userEvent.setup()
      render(<InputField label="Password" type="password" />)
      const initial = screen.getByRole('button', { name: 'Show password' })
      await user.click(initial)
      await user.click(screen.getByRole('button', { name: 'Hide password' }))
      expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password')
    })

    it('activates the toggle via keyboard (Enter and Space)', async () => {
      const user = userEvent.setup()
      render(<InputField label="Password" type="password" />)
      const button = screen.getByRole('button', { name: 'Show password' })
      button.focus()
      await user.keyboard('{Enter}')
      expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'text')
      screen.getByRole('button', { name: 'Hide password' }).focus()
      await user.keyboard(' ')
      expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password')
    })

    it('captures typed value while the field is revealed', async () => {
      const user = userEvent.setup()
      render(<InputField label="Password" type="password" />)
      await user.click(screen.getByRole('button', { name: 'Show password' }))
      const input = screen.getByLabelText('Password')
      await user.type(input, 'SecurePass123')
      expect(input).toHaveValue('SecurePass123')
    })

    it('does not render the toggle when hidePasswordToggle is set', () => {
      render(<InputField label="Password" type="password" hidePasswordToggle />)
      expect(screen.queryByRole('button', { name: 'Show password' })).not.toBeInTheDocument()
    })

    it('does not render the toggle when caller supplies trailingElement', () => {
      render(
        <InputField
          label="Password"
          type="password"
          trailingElement={<span data-testid="custom-trail">x</span>}
        />,
      )
      expect(screen.queryByRole('button', { name: 'Show password' })).not.toBeInTheDocument()
      expect(screen.getByTestId('custom-trail')).toBeInTheDocument()
    })

    it('does not render the toggle on non-password fields', () => {
      render(<InputField label="Username" type="text" />)
      expect(screen.queryByRole('button', { name: 'Show password' })).not.toBeInTheDocument()
    })

    it('disables the toggle when the field is disabled', () => {
      render(<InputField label="Password" type="password" disabled />)
      expect(screen.getByRole('button', { name: 'Show password' })).toBeDisabled()
    })

    it('does not submit a wrapping form when clicked', async () => {
      const user = userEvent.setup()
      const onSubmit = vi.fn((e: React.FormEvent) => e.preventDefault())
      render(
        <form onSubmit={onSubmit}>
          <InputField label="Password" type="password" />
        </form>,
      )
      await user.click(screen.getByRole('button', { name: 'Show password' }))
      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('shares visibility across fields inside PasswordVisibilityGroup', async () => {
      const user = userEvent.setup()
      render(
        <PasswordVisibilityGroup>
          <InputField label="Password" type="password" />
          <InputField label="Confirm Password" type="password" />
        </PasswordVisibilityGroup>,
      )
      const toggles = screen.getAllByRole('button', { name: 'Show password' })
      expect(toggles).toHaveLength(2)
      const [firstToggle] = toggles
      if (!firstToggle) throw new Error('expected toggle button')
      await user.click(firstToggle)
      expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'text')
      expect(screen.getByLabelText('Confirm Password')).toHaveAttribute('type', 'text')
      expect(screen.getAllByRole('button', { name: 'Hide password' })).toHaveLength(2)
    })

    it('keeps fields independent when not wrapped in a group', async () => {
      const user = userEvent.setup()
      render(
        <>
          <InputField label="Password" type="password" />
          <InputField label="Confirm Password" type="password" />
        </>,
      )
      const toggles = screen.getAllByRole('button', { name: 'Show password' })
      const [firstToggle] = toggles
      if (!firstToggle) throw new Error('expected toggle button')
      await user.click(firstToggle)
      expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'text')
      expect(screen.getByLabelText('Confirm Password')).toHaveAttribute('type', 'password')
    })
  })
})
