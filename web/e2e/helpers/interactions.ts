/**
 * Shared Playwright interaction helpers for E2E flow tests.
 *
 * These wrap Playwright primitives with the project's preferred
 * wait-for patterns. Notably we never use ``page.waitForTimeout``;
 * every wait sits on a selector or a network response.
 */

import type { Locator, Page } from '@playwright/test'

/**
 * Escape regex metacharacters in a string so it matches literally.
 *
 * Without this, labels like ``"C++"`` or ``"Name?"`` would be
 * interpreted as regex syntax (``+`` quantifier, ``?`` optional) and
 * either fail to match or throw at construction time. The escape
 * pattern is the standard one from MDN; covers ``. * + ? ^ $ { } ( )
 * | [ ] \``.
 */
function escapeRegExp(literal: string): string {
  return literal.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * Drag *source* onto *target* using Playwright's HTML5 DnD API.
 *
 * Only works for surfaces wired to the native HTML5 drag events
 * (``dragstart`` / ``dragover`` / ``drop``). The Kanban board and the
 * org-edit agent board use ``@dnd-kit`` with a ``PointerSensor``, which
 * ignores HTML5 drag entirely -- use {@link pointerDragTo} for those.
 */
export async function dragTo(source: Locator, target: Locator): Promise<void> {
  await source.dragTo(target)
}

/**
 * Drag *source* onto *target* by simulating raw pointer movement.
 *
 * ``@dnd-kit``'s ``PointerSensor`` activates only after the pointer
 * travels past its ``activationConstraint`` distance (8px on the task
 * board), then tracks ``pointermove`` to compute the ``over`` droppable
 * via collision detection. Playwright's HTML5 ``dragTo`` never fires
 * those pointer events, so this helper drives the gesture manually:
 * press on the source centre, nudge past the activation threshold, glide
 * to the target centre in steps (so collision detection registers the
 * hovered droppable), then release. ``steps`` keeps the move continuous
 * rather than teleporting, which @dnd-kit needs to settle ``over``.
 */
export async function pointerDragTo(
  page: Page,
  source: Locator,
  target: Locator,
): Promise<void> {
  await source.waitFor({ state: 'visible' })
  await target.waitFor({ state: 'visible' })
  const from = await source.boundingBox()
  const to = await target.boundingBox()
  if (!from) {
    throw new Error('pointerDragTo: source element has no bounding box')
  }
  if (!to) {
    throw new Error('pointerDragTo: target element has no bounding box')
  }
  const startX = from.x + from.width / 2
  const startY = from.y + from.height / 2
  const endX = to.x + to.width / 2
  const endY = to.y + to.height / 2

  await page.mouse.move(startX, startY)
  await page.mouse.down()
  // Cross the 8px activation threshold before heading for the target so
  // the sensor engages a drag rather than treating the press as a click.
  await page.mouse.move(startX + 12, startY, { steps: 4 })
  await page.mouse.move(endX, endY, { steps: 12 })
  // A second settle move on the target lets collision detection lock in
  // the hovered droppable before the drop commits.
  await page.mouse.move(endX, endY, { steps: 2 })
  await page.mouse.up()
}

/**
 * Fill every entry in *fields* by accessible label.
 *
 * Convenience wrapper for multi-field forms (setup wizard, agent
 * creation). Each key is a label (regex or string) and each value is
 * the text to type. Labels are matched literally (case-insensitive)
 * so labels with regex metacharacters (``C++``, ``Name?``) work.
 */
export async function fillForm(
  page: Page,
  fields: Record<string, string>,
): Promise<void> {
  for (const [label, value] of Object.entries(fields)) {
    await page.getByLabel(new RegExp(escapeRegExp(label), 'i')).fill(value)
  }
}

/**
 * Click a button identified by its accessible name.
 */
export async function clickButton(page: Page, name: string | RegExp): Promise<void> {
  await page.getByRole('button', { name }).click()
}

/**
 * Click a pre-resolved Locator.
 *
 * Some surfaces don't expose buttons by accessible name (e.g. Kanban
 * cards rendered as ``<div>`` with click handlers); the test still
 * needs the wait-for-visibility-then-click pattern that the other
 * helpers enforce. This wrapper accepts an already-located element
 * and runs the same ``waitForVisibility -> click`` sequence so flow
 * specs do not call ``locator.click()`` directly.
 */
export async function clickLocator(target: Locator): Promise<void> {
  await target.waitFor({ state: 'visible' })
  await target.click()
}

/**
 * Click a button and wait for a network response matching *urlPattern*.
 *
 * The dashboard often kicks off API calls on click; this helper makes
 * that wait explicit so tests do not race ahead of the response.
 */
export async function clickAndAwait(
  page: Page,
  buttonName: string | RegExp,
  urlPattern: string | RegExp,
): Promise<void> {
  await Promise.all([
    page.waitForResponse(urlPattern),
    page.getByRole('button', { name: buttonName }).click(),
  ])
}

/**
 * Select an option in a labeled native ``<select>``.
 *
 * The project deliberately keeps native ``<select>`` for accessibility
 * and mobile UX, so this helper uses ``selectOption`` rather than the
 * combobox role queries Base UI / shadcn surfaces would need.
 */
export async function selectOption(
  page: Page,
  label: string | RegExp,
  optionLabel: string,
): Promise<void> {
  await page
    .getByLabel(
      typeof label === 'string' ? new RegExp(escapeRegExp(label), 'i') : label,
    )
    .selectOption({ label: optionLabel })
}
