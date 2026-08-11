import { render, screen } from '@testing-library/react'

import { SpendMeasurabilityNotice } from '@/pages/budget/SpendMeasurabilityNotice'

describe('SpendMeasurabilityNotice', () => {
  it('warns that the money ceiling cannot bind when nothing is measurable', () => {
    render(<SpendMeasurabilityNotice measurability="unmeasurable" />)
    const notice = screen.getByRole('status')
    expect(notice).toHaveTextContent(/not measurable/i)
    expect(notice).toHaveTextContent(/token ceiling/i)
  })

  it('says the total understates when only some providers are metered', () => {
    render(<SpendMeasurabilityNotice measurability="mixed" />)
    expect(screen.getByRole('status')).toHaveTextContent(/understate/i)
  })

  it('renders nothing when every record was metered', () => {
    render(<SpendMeasurabilityNotice measurability="measured" />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('renders nothing while the period is still loading', () => {
    render(<SpendMeasurabilityNotice measurability={undefined} />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})
