import { screen } from '@testing-library/react'
import { AppShellBody } from '@/components/layout/AppLayout'
import { renderWithRouter } from '../../test-utils'

// The nav column itself is not under test here: what is, is whether the shell
// paints one before it knows how wide it should be. Stubbing it keeps this off
// the sidebar's own store, breakpoint and polling dependencies.
vi.mock('@/components/layout/Sidebar', () => ({
  Sidebar: () => <nav aria-label="Main navigation">Nav</nav>,
}))

function renderBody(layoutReady: boolean) {
  return renderWithRouter(
    <AppShellBody
      layoutReady={layoutReady}
      pathname="/"
      sidebarOverlayOpen={false}
      onSidebarOverlayClose={() => undefined}
    />,
  )
}

describe('the shell body before its layout preferences arrive', () => {
  it('paints no nav column while its width is still unknown', () => {
    // Both preferences deciding the column's width are backend-owned and land
    // after the shell mounts. Painting the defaults and correcting them slides
    // the content column sideways a beat into the session, under whatever the
    // operator is already reaching for.
    renderBody(false)

    expect(screen.queryByRole('navigation', { name: 'Main navigation' })).toBeNull()
    expect(document.querySelector('#main-content')).toBeNull()
  })

  it('says the page is still loading rather than showing an empty one', () => {
    renderBody(false)

    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('paints both columns once the width is known', () => {
    renderBody(true)

    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument()
    expect(document.querySelector('#main-content')).not.toBeNull()
  })
})
