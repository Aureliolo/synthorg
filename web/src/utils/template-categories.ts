/** Template categorization for the setup wizard. */

import type { TemplateInfoResponse } from '@/api/types/setup'

/**
 * Direct mapping from template tag to canonical category key.
 * Templates are categorized by the first tag that matches a known category.
 */
const TAG_TO_CATEGORY: Readonly<Record<string, string>> = {
  startup: 'startup',
  solo: 'startup',
  'small-team': 'startup',
  mvp: 'startup',
  'dev-shop': 'dev-shop',
  engineering: 'dev-shop',
  'ci-cd': 'dev-shop',
  'code-review': 'dev-shop',
  devops: 'dev-shop',
  product: 'product',
  'design-system': 'product',
  data: 'data',
  analytics: 'data',
  ml: 'data',
  pipelines: 'data',
  marketing: 'marketing',
  content: 'marketing',
  growth: 'marketing',
  campaigns: 'marketing',
  security: 'security',
  'red-team': 'security',
  support: 'support',
  'customer-facing': 'support',
  enterprise: 'enterprise',
  'full-company': 'enterprise',
  consultancy: 'consultancy',
  consulting: 'consultancy',
  advisory: 'consultancy',
  agency: 'consultancy',
  research: 'research',
}

/**
 * Canonical category key to display label.
 * Used by getCategoryLabel for O(1) lookup.
 */
const CATEGORY_TO_LABEL: Readonly<Record<string, string>> = {
  startup: 'Startup',
  'dev-shop': 'Development',
  product: 'Product',
  data: 'Data & Analytics',
  marketing: 'Marketing',
  security: 'Security',
  support: 'Support',
  enterprise: 'Enterprise',
  consultancy: 'Professional Services',
  research: 'Research',
  other: 'Other',
}

/** Ordered list of category keys for display. */
export const CATEGORY_ORDER: readonly string[] = [
  'startup',
  'dev-shop',
  'product',
  'data',
  'marketing',
  'security',
  'support',
  'enterprise',
  'consultancy',
  'research',
  'other',
]

/**
 * Derive the canonical category key from a list of tags.
 * Returns the first matching category, or 'other' if no match.
 */
export function deriveCategoryFromTags(tags: readonly string[]): string {
  for (const tag of tags) {
    const category = TAG_TO_CATEGORY[tag]
    if (category) return category
  }
  return 'other'
}

/** Get the canonical category key for a template based on its tags. */
function getTemplateCategory(template: TemplateInfoResponse): string {
  return deriveCategoryFromTags(template.tags)
}

/**
 * Group templates into ordered categories based on their tags.
 *
 * Returns a Map with category keys in CATEGORY_ORDER.
 * Categories with no templates are omitted.
 */
export function categorizeTemplates(
  templates: readonly TemplateInfoResponse[],
): Map<string, TemplateInfoResponse[]> {
  const groups = new Map<string, TemplateInfoResponse[]>()

  for (const template of templates) {
    const category = getTemplateCategory(template)
    const existing = groups.get(category)
    if (existing) {
      existing.push(template)
    } else {
      groups.set(category, [template])
    }
  }

  // Reorder to match CATEGORY_ORDER
  const ordered = new Map<string, TemplateInfoResponse[]>()
  for (const key of CATEGORY_ORDER) {
    const items = groups.get(key)
    if (items) {
      ordered.set(key, items)
    }
  }

  // Add any categories not in CATEGORY_ORDER at the end
  for (const [key, items] of groups) {
    if (!ordered.has(key)) {
      ordered.set(key, items)
    }
  }

  return ordered
}

/**
 * Get a human-readable label for a category key.
 */
export function getCategoryLabel(category: string): string {
  const label = CATEGORY_TO_LABEL[category]
  if (label) return label
  // Title-case the key as fallback, splitting on `-` and `_`
  return category
    .split(/[-_]/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}
