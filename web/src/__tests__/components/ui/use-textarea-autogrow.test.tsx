import { render } from '@testing-library/react'
import { useRef } from 'react'
import { describe, expect, it } from 'vitest'

import { useTextareaAutogrow } from '@/components/ui/use-textarea-autogrow'

function Harness({ value }: { value: string }) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useTextareaAutogrow(ref, value)
  return <textarea ref={ref} data-testid="ta" defaultValue={value} />
}

describe('useTextareaAutogrow', () => {
  it('disables the manual resize handle and drives height inline', () => {
    const { getByTestId } = render(<Harness value="hi" />)
    const ta = getByTestId('ta') as HTMLTextAreaElement
    expect(ta.style.resize).toBe('none')
    expect(ta.style.height).not.toBe('')
  })

  it('switches to internal scroll once content exceeds the row cap', () => {
    const { getByTestId, rerender } = render(<Harness value="x" />)
    const ta = getByTestId('ta') as HTMLTextAreaElement
    // jsdom does not lay out text, so force a tall scrollHeight to exercise the
    // cap: beyond the max rows the field must scroll internally, not grow on.
    Object.defineProperty(ta, 'scrollHeight', { configurable: true, value: 10_000 })
    rerender(<Harness value="a genuinely very long multi-line paragraph" />)
    expect(ta.style.overflowY).toBe('auto')
  })

  it('hides overflow while the content still fits', () => {
    const { getByTestId, rerender } = render(<Harness value="x" />)
    const ta = getByTestId('ta') as HTMLTextAreaElement
    Object.defineProperty(ta, 'scrollHeight', { configurable: true, value: 1 })
    rerender(<Harness value="short" />)
    expect(ta.style.overflowY).toBe('hidden')
  })
})
