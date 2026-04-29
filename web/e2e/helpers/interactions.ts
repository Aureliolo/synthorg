/**
 * Shared Playwright interaction helpers for E2E flow tests.
 *
 * These wrap Playwright primitives with the project's preferred
 * wait-for patterns. Notably we never use ``page.waitForTimeout`` --
 * every wait sits on a selector or a network response.
 *
 * Issue #1604 / W5a.
 */

import type { Locator, Page } from '@playwright/test'

/**
 * Drag *source* onto *target* using Playwright's HTML5 DnD API.
 *
 * Used by the Kanban board test to move a task card between columns.
 */
export async function dragTo(source: Locator, target: Locator): Promise<void> {
  await source.dragTo(target)
}

/**
 * Fill every entry in *fields* by accessible label.
 *
 * Convenience wrapper for multi-field forms (setup wizard, agent
 * creation). Each key is a label (regex or string) and each value is
 * the text to type.
 */
export async function fillForm(
  page: Page,
  fields: Record<string, string>,
): Promise<void> {
  for (const [label, value] of Object.entries(fields)) {
    await page.getByLabel(new RegExp(label, 'i')).fill(value)
  }
}

/**
 * Click a button identified by its accessible name.
 */
export async function clickButton(page: Page, name: string | RegExp): Promise<void> {
  await page.getByRole('button', { name }).click()
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
    .getByLabel(typeof label === 'string' ? new RegExp(label, 'i') : label)
    .selectOption({ label: optionLabel })
}
