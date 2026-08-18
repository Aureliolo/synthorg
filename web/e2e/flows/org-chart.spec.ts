import { test, expect, type Locator, type Page } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness } from '../fixtures/websocket-harness'
import { makeCompanyConfig, makeDepartment, makeOrgAgent } from '../factories'

/**
 * Critical-flow E2E: the org chart's laid-out geometry in a real browser.
 *
 * The unit suite asserts the layout maths; this asserts what actually lands
 * on the canvas once React Flow has applied it. Two properties are checked
 * against the seeded company: departments read left to right in the order the
 * operator arranged them, and a department with more reports than fit in a
 * row wraps them into a block beneath its lead rather than one long strip.
 *
 * Every box is read through ``getBoundingClientRect()``, so all of it is in
 * screen space after React Flow's fit-to-view transform. That transform is a
 * uniform scale plus a translate, which preserves ordering and alignment; only
 * absolute pixel sizes would be meaningless, and none are asserted.
 */

const NARROW_DEPT = 'ops'
const WIDE_DEPT = 'engineering'

function agentsFor(department: string, names: readonly string[], headRole: string) {
  return names.map((name, index) =>
    makeOrgAgent({
      id: `agent-${name}`,
      name,
      department,
      role: index === 0 ? headRole : 'Developer',
    }),
  )
}

const COMPANY = makeCompanyConfig({
  departments: [
    makeDepartment({ name: 'executive', display_name: 'Executive', head: 'CEO' }),
    makeDepartment({ name: NARROW_DEPT, display_name: 'Ops', head: 'Head of ops' }),
    makeDepartment({
      name: WIDE_DEPT,
      display_name: 'Engineering',
      head: 'Head of engineering',
    }),
  ],
  agents: [
    ...agentsFor('executive', ['zoe'], 'CEO'),
    ...agentsFor(NARROW_DEPT, ['ola', 'oscar', 'olive'], 'Head of ops'),
    ...agentsFor(
      WIDE_DEPT,
      ['alice', 'bob', 'carol', 'dave', 'eve', 'frank', 'grace', 'heidi'],
      'Head of engineering',
    ),
  ],
})

interface Box {
  x: number
  y: number
  width: number
  height: number
}

function node(page: Page, id: string): Locator {
  return page.locator(`.react-flow__node[data-id="${id}"]`)
}

/**
 * Measure several nodes in one frame.
 *
 * React Flow animates its fit-to-view transform, so reading each node with
 * its own ``boundingBox()`` round-trip samples them at different points in
 * that animation, and two nodes genuinely sharing an edge come back several
 * pixels apart. One ``page.evaluate`` reads every rect against one transform.
 */
async function boxesOf(page: Page, ids: readonly string[]): Promise<Record<string, Box>> {
  const boxes = await page.evaluate((nodeIds) => {
    const out: Record<string, Box | null> = {}
    for (const id of nodeIds) {
      const el = document.querySelector(`.react-flow__node[data-id="${id}"]`)
      const rect = el?.getBoundingClientRect()
      out[id] = rect ? { x: rect.x, y: rect.y, width: rect.width, height: rect.height } : null
    }
    return out
  }, [...ids])
  const measured: Record<string, Box> = {}
  for (const id of ids) {
    const box = boxes[id]
    expect(box, `node ${id} is rendered and measurable`).not.toBeNull()
    measured[id] = box!
  }
  return measured
}

test.describe('Org chart layout', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
    await page.route('**/api/v1/company', async (route) => {
      if (route.request().method() !== 'GET') {
        await route.fallback()
        return
      }
      await route.fulfill({
        json: { success: true, data: COMPANY, error: null, error_detail: null },
      })
    })
    await page.goto('/org')
    await expect(node(page, `dept-${WIDE_DEPT}`)).toBeVisible()
  })

  test('reads departments left to right in the configured order', async ({ page }) => {
    const boxes = await boxesOf(page, [`dept-${NARROW_DEPT}`, `dept-${WIDE_DEPT}`])
    expect(boxes[`dept-${NARROW_DEPT}`]!.x).toBeLessThan(boxes[`dept-${WIDE_DEPT}`]!.x)
  })

  test('keeps the root department above the departments that report to it', async ({ page }) => {
    const boxes = await boxesOf(page, ['dept-executive', `dept-${NARROW_DEPT}`])
    const root = boxes['dept-executive']!
    expect(root.y + root.height).toBeLessThanOrEqual(boxes[`dept-${NARROW_DEPT}`]!.y)
  })

  test('stacks a narrow department beneath its lead', async ({ page }) => {
    const boxes = await boxesOf(page, ['agent-ola', 'agent-oscar', 'agent-olive'])
    const [lead, first, second] = [boxes['agent-ola']!, boxes['agent-oscar']!, boxes['agent-olive']!]
    expect(lead.y).toBeLessThan(first.y)
    expect(first.y).toBeCloseTo(second.y, 1)
    expect(first.x).toBeLessThan(second.x)
  })

  test('wraps a wide department into a block beneath its lead', async ({ page }) => {
    // Seven reports under alice, so the column rule gives three per row and
    // fills row-major: bob/carol/dave, then eve/frank/grace, then heidi.
    const boxes = await boxesOf(page, [
      'agent-alice',
      'agent-bob',
      'agent-carol',
      'agent-dave',
      'agent-eve',
      'agent-heidi',
    ])
    const [lead, bob, carol, dave, eve, heidi] = [
      boxes['agent-alice']!,
      boxes['agent-bob']!,
      boxes['agent-carol']!,
      boxes['agent-dave']!,
      boxes['agent-eve']!,
      boxes['agent-heidi']!,
    ]
    // The lead sits above the block, not beside it.
    expect(lead.y).toBeLessThan(bob.y)
    // First row: three abreast, in the operator's own order.
    expect(bob.y).toBeCloseTo(carol.y, 1)
    expect(carol.y).toBeCloseTo(dave.y, 1)
    expect(bob.x).toBeLessThan(carol.x)
    expect(carol.x).toBeLessThan(dave.x)
    // Row two starts under row one's first column, and row three under that.
    expect(bob.y).toBeLessThan(eve.y)
    expect(eve.x).toBeCloseTo(bob.x, 1)
    expect(eve.y).toBeLessThan(heidi.y)
  })

  test('keeps a wide department from dominating the row', async ({ page }) => {
    const boxes = await boxesOf(page, [`dept-${NARROW_DEPT}`, `dept-${WIDE_DEPT}`])
    // Eight members laid out in a row would be several times the width of a
    // three-member department; in a column it stays comparable.
    expect(boxes[`dept-${WIDE_DEPT}`]!.width).toBeLessThan(boxes[`dept-${NARROW_DEPT}`]!.width * 2)
  })
})
