import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SelectField } from '@/components/ui/select-field'

const options = [
  { value: 'EUR', label: 'EUR - Euro' },
  { value: 'USD', label: 'USD - US Dollar' },
  { value: 'GBP', label: 'GBP - British Pound' },
]

describe('SelectField', () => {
  it('renders label text', () => {
    render(<SelectField label="Currency" options={options} value="EUR" onChange={() => {}} />)
    expect(screen.getByLabelText('Currency')).toBeInTheDocument()
  })

  it('renders all options', () => {
    render(<SelectField label="Currency" options={options} value="EUR" onChange={() => {}} />)
    expect(screen.getAllByRole('option')).toHaveLength(3)
  })

  it('renders placeholder option when provided', () => {
    render(
      <SelectField
        label="Currency"
        options={options}
        value=""
        onChange={() => {}}
        placeholder="Select..."
      />,
    )
    expect(screen.getAllByRole('option')).toHaveLength(4)
    expect(screen.getByText('Select...')).toBeInTheDocument()
  })

  // A native <select> whose value matches no <option> displays the first option
  // instead, so without a matching option the control reports a selection the
  // form state does not hold and a required-field validator rejects what the
  // operator can see is filled in.
  it('does not display the first option as selected when the value is unset', () => {
    render(<SelectField label="Currency" options={options} value="" onChange={() => {}} />)
    expect(screen.getByLabelText<HTMLSelectElement>('Currency').value).toBe('')
  })

  it('renders a fallback option for an unset value with no placeholder', () => {
    render(<SelectField label="Currency" options={options} value="" onChange={() => {}} />)
    expect(screen.getAllByRole('option')).toHaveLength(4)
    // Asserted by text, not just by count: the count alone passes even if the
    // label goes blank, which is the visible half of this behaviour.
    expect(screen.getByText('Select an option')).toBeInTheDocument()
  })

  it('marks the fallback option unselectable', () => {
    // The value is not one of the real choices, so offering it as one would let
    // the operator "pick" a value the form does not accept.
    render(<SelectField label="Currency" options={options} value="CHF" onChange={() => {}} />)
    expect(screen.getByRole('option', { name: 'CHF' })).toBeDisabled()
  })

  it('describes an unmatched value as unavailable for assistive tech', () => {
    // A screen-reader user tabbing to the closed control otherwise hears only
    // the value, with nothing saying it is not among the choices.
    render(<SelectField label="Currency" options={options} value="CHF" onChange={() => {}} />)
    const select = screen.getByLabelText<HTMLSelectElement>('Currency')
    const describedBy = select.getAttribute('aria-describedby')
    expect(describedBy).not.toBeNull()
    expect(document.getElementById(describedBy ?? '')?.textContent).toContain(
      'not available',
    )
  })

  it('names an opaque stale value the way the operator would', () => {
    // An option value is usually its own name, but it can be an encoded key:
    // the agent model picker packs a {provider, modelId} pair into one, and
    // this note read the JSON out at the operator.
    render(
      <SelectField
        label="Model"
        options={options}
        value={'{"provider":"example-provider","modelId":"example-capable-001"}'}
        staleValueLabel="example-provider/example-capable-001"
        onChange={() => {}}
      />,
    )
    expect(
      screen.getByText(/"example-provider\/example-capable-001" is not available/),
    ).toBeInTheDocument()
    expect(screen.queryByText(/modelId/)).not.toBeInTheDocument()
  })

  it('adds no unavailable-value hint when the value matches an option', () => {
    render(<SelectField label="Currency" options={options} value="EUR" onChange={() => {}} />)
    expect(screen.queryByText(/not available/)).not.toBeInTheDocument()
  })

  it('shows an unmatched value as itself rather than another option', () => {
    render(<SelectField label="Currency" options={options} value="CHF" onChange={() => {}} />)
    const select = screen.getByLabelText<HTMLSelectElement>('Currency')
    expect(select.value).toBe('CHF')
    expect(screen.getByText('CHF')).toBeInTheDocument()
  })

  it('adds no fallback option when the value matches a grouped option', () => {
    render(
      <SelectField
        label="Currency"
        groups={[{ label: 'Europe', options: [{ value: 'EUR', label: 'EUR - Euro' }] }]}
        value="EUR"
        onChange={() => {}}
      />,
    )
    expect(screen.getAllByRole('option')).toHaveLength(1)
  })

  it('adds a fallback option when the value matches no grouped option', () => {
    render(
      <SelectField
        label="Currency"
        groups={[{ label: 'Europe', options: [{ value: 'EUR', label: 'EUR - Euro' }] }]}
        value=""
        onChange={() => {}}
      />,
    )
    expect(screen.getByLabelText<HTMLSelectElement>('Currency').value).toBe('')
  })

  it('calls onChange with selected value', async () => {
    const handleChange = vi.fn()
    const user = userEvent.setup()
    render(<SelectField label="Currency" options={options} value="EUR" onChange={handleChange} />)
    await user.selectOptions(screen.getByLabelText('Currency'), 'USD')
    expect(handleChange).toHaveBeenCalledWith('USD')
  })

  it('renders error message', () => {
    render(
      <SelectField label="Currency" options={options} value="" onChange={() => {}} error="Required" />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Required')
  })

  it('sets aria-invalid when error is present', () => {
    render(
      <SelectField label="Currency" options={options} value="" onChange={() => {}} error="Required" />,
    )
    expect(screen.getByLabelText('Currency')).toHaveAttribute('aria-invalid', 'true')
  })

  it('renders required indicator', () => {
    render(
      <SelectField label="Currency" options={options} value="EUR" onChange={() => {}} required />,
    )
    expect(screen.getByText('*')).toBeInTheDocument()
  })

  it('respects disabled state', () => {
    render(
      <SelectField label="Currency" options={options} value="EUR" onChange={() => {}} disabled />,
    )
    expect(screen.getByLabelText('Currency')).toBeDisabled()
  })
})
