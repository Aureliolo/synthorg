import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { CoverageField } from '@/pages/plans/PlanEditorCoverage'

const CRITERIA = [
  'R01: A player can play a full game',
  'R02: The board renders at sixty frames a second',
]

/**
 * What an item advances is now enforced at both write boundaries, so the
 * operator needs a control that can reach it. There was none: the field was
 * carried through the editor verbatim and rendered nowhere, while a PATCH
 * re-validates the whole item array, so a plan written before the rule became
 * un-editable over text no control could touch.
 */
describe('CoverageField', () => {
  it('offers every criterion the plan states', () => {
    render(
      <CoverageField
        index={0}
        satisfies={[]}
        objectiveCriteria={CRITERIA}
        onChange={vi.fn()}
      />,
    )

    for (const criterion of CRITERIA) {
      expect(screen.getByText(criterion)).toBeInTheDocument()
    }
  })

  it('ticks the criteria the item already claims', () => {
    render(
      <CoverageField
        index={0}
        satisfies={[CRITERIA[1] as string]}
        objectiveCriteria={CRITERIA}
        onChange={vi.fn()}
      />,
    )

    const boxes = screen.getAllByRole('checkbox')
    expect(boxes[0]).not.toBeChecked()
    expect(boxes[1]).toBeChecked()
  })

  it('claims a criterion when its box is ticked', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <CoverageField
        index={3}
        satisfies={[]}
        objectiveCriteria={CRITERIA}
        onChange={onChange}
      />,
    )

    await user.click(screen.getAllByRole('checkbox')[0] as HTMLElement)

    expect(onChange).toHaveBeenCalledWith(3, { satisfies: [CRITERIA[0]] })
  })

  it('drops a criterion when its box is unticked', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <CoverageField
        index={0}
        satisfies={CRITERIA}
        objectiveCriteria={CRITERIA}
        onChange={onChange}
      />,
    )

    await user.click(screen.getAllByRole('checkbox')[0] as HTMLElement)

    expect(onChange).toHaveBeenCalledWith(0, { satisfies: [CRITERIA[1]] })
  })

  it('matches a claim differing only in case and spacing', () => {
    render(
      <CoverageField
        index={0}
        satisfies={['  R01:   a PLAYER can play a full game ']}
        objectiveCriteria={CRITERIA}
        onChange={vi.fn()}
      />,
    )

    expect(screen.getAllByRole('checkbox')[0]).toBeChecked()
  })

  it('shows a claim naming nothing so it can be cleared', () => {
    // The case that makes an old plan editable again: the backend refuses the
    // whole array on save, and a claim with no control is one the operator can
    // only escape by deleting the item.
    render(
      <CoverageField
        index={0}
        satisfies={['it feels good to play']}
        objectiveCriteria={CRITERIA}
        onChange={vi.fn()}
      />,
    )

    expect(screen.getByText(/it feels good to play/)).toBeInTheDocument()
    expect(screen.getByText(/untick to clear/)).toBeInTheDocument()
  })

  it('clears a claim naming nothing when it is unticked', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <CoverageField
        index={0}
        satisfies={['it feels good to play']}
        objectiveCriteria={CRITERIA}
        onChange={onChange}
      />,
    )

    await user.click(screen.getAllByRole('checkbox')[2] as HTMLElement)

    expect(onChange).toHaveBeenCalledWith(0, { satisfies: [] })
  })

  it('renders nothing when the plan states no criteria and none are claimed', () => {
    const { container } = render(
      <CoverageField
        index={0}
        satisfies={[]}
        objectiveCriteria={[]}
        onChange={vi.fn()}
      />,
    )

    expect(container).toBeEmptyDOMElement()
  })
})
