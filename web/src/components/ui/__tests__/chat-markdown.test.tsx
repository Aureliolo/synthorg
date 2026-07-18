import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ChatMarkdown } from '../chat-markdown'

describe('ChatMarkdown', () => {
  it('renders inline markdown as semantic elements', () => {
    const { container } = render(
      <ChatMarkdown content="Runway is **7 months** at current burn." />,
    )
    const strong = container.querySelector('strong')
    expect(strong?.textContent).toBe('7 months')
  })

  it('renders lists and GFM tables', () => {
    const { container } = render(
      <ChatMarkdown
        content={['- first', '- second', '', '| A | B |', '| - | - |', '| 1 | 2 |'].join(
          '\n',
        )}
      />,
    )
    expect(container.querySelectorAll('ul li')).toHaveLength(2)
    expect(container.querySelector('table')).not.toBeNull()
    expect(container.querySelectorAll('tbody td')).toHaveLength(2)
  })

  it('strips script tags and inline event handlers the model emits', () => {
    const { container } = render(
      <ChatMarkdown
        content={'Safe. <img src=x onerror="alert(1)"> <script>alert(1)</script> Still safe.'}
      />,
    )
    // Sanitisation removes executable markup entirely: no script element and
    // no image carrying an event handler survives into the DOM.
    expect(container.querySelector('script')).toBeNull()
    const withHandler = Array.from(container.querySelectorAll('*')).find((el) =>
      el.getAttributeNames().some((n) => n.startsWith('on')),
    )
    expect(withHandler).toBeUndefined()
    expect(container.textContent).toContain('Still safe.')
  })
})
