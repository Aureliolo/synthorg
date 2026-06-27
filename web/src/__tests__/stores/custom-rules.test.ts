import { http, HttpResponse } from 'msw'
import { beforeEach, describe } from 'vitest'
import { useCustomRulesStore } from '@/stores/custom-rules'
import { useToastStore } from '@/stores/toast'
import { apiError, buildCustomRule, successFor } from '@/mocks/handlers'
import type {
  createCustomRule as createCustomRuleApi,
  toggleCustomRule as toggleCustomRuleApi,
  updateCustomRule as updateCustomRuleApi,
} from '@/api/endpoints/custom-rules'
import { server } from '@/test-setup'

describe('useCustomRulesStore mutations', () => {
  beforeEach(() => {
    useCustomRulesStore.setState({
      rules: [],
      submitting: false,
      error: null,
    })
    // Global afterEach in test-setup.tsx already runs dismissAll +
    // cancelPendingPersist -- do NOT duplicate here or the async-leak
    // detector counts the double-tear-down as churn.
    useToastStore.getState().dismissAll()
  })

  describe('createRule', () => {
    it('returns the new rule + emits success toast', async () => {
      const rule = buildCustomRule({ id: 'rule-1', name: 'rule-1' })
      server.use(
        http.post('/api/v1/meta/custom-rules', () =>
          HttpResponse.json(successFor<typeof createCustomRuleApi>(rule), {
            status: 201,
          }),
        ),
      )

      const result = await useCustomRulesStore.getState().createRule({
        name: 'rule-1',
        description: '',
        metric_path: 'avg_quality_score',
        comparator: 'lt',
        threshold: 0.5,
        severity: 'warning',
        target_altitudes: ['config_tuning'],
      })

      expect(result?.id).toBe('rule-1')
      expect(useCustomRulesStore.getState().rules).toContainEqual(rule)
      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.variant).toBe('success')
    })

    it('returns null + emits error toast on failure', async () => {
      server.use(
        http.post('/api/v1/meta/custom-rules', () =>
          HttpResponse.json(apiError('bad'), { status: 400 }),
        ),
      )

      const result = await useCustomRulesStore.getState().createRule({
        name: 'rule-1',
        description: '',
        metric_path: 'avg_quality_score',
        comparator: 'lt',
        threshold: 0.5,
        severity: 'warning',
        target_altitudes: ['config_tuning'],
      })

      expect(result).toBeNull()
      expect(useCustomRulesStore.getState().rules).toEqual([])
      expect(useCustomRulesStore.getState().submitting).toBe(false)
      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.variant).toBe('error')
      expect(toasts[0]!.title).toBe('Failed to create rule')
    })
  })

  describe('updateRule', () => {
    it('returns null + emits error toast on failure', async () => {
      server.use(
        http.patch('/api/v1/meta/custom-rules/:id', () =>
          HttpResponse.json(apiError('bad'), { status: 400 }),
        ),
      )

      const result = await useCustomRulesStore
        .getState()
        .updateRule('rule-1', { description: 'x' })

      expect(result).toBeNull()
      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.title).toBe('Failed to update rule')
    })

    it('replaces rule + emits success toast on success', async () => {
      const original = buildCustomRule({ id: 'rule-1', description: 'old' })
      const updated = buildCustomRule({ id: 'rule-1', description: 'new' })
      useCustomRulesStore.setState({ rules: [original] })
      server.use(
        http.patch('/api/v1/meta/custom-rules/:id', () =>
          HttpResponse.json(successFor<typeof updateCustomRuleApi>(updated)),
        ),
      )

      const result = await useCustomRulesStore
        .getState()
        .updateRule('rule-1', { description: 'new' })

      expect(result?.description).toBe('new')
      expect(useCustomRulesStore.getState().rules).toEqual([updated])
      const successToasts = useToastStore
        .getState()
        .toasts.filter((t) => t.variant === 'success')
      expect(successToasts).toHaveLength(1)
      // Format: `Rule <name> updated` -- lock the suffix only so the
      // assertion stays robust to fixture renaming.
      expect(successToasts[0]!.title).toMatch(/ updated$/)
    })
  })

  describe('deleteRule', () => {
    it('returns false + emits error toast on failure', async () => {
      server.use(
        http.delete('/api/v1/meta/custom-rules/:id', () =>
          HttpResponse.json(apiError('not found'), { status: 404 }),
        ),
      )

      const result = await useCustomRulesStore.getState().deleteRule('rule-1')

      expect(result).toBe(false)
      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.variant).toBe('error')
      // ``getCrudErrorTitle`` maps the 404 to a category-aware title rather
      // than the generic delete fallback.
      expect(toasts[0]!.title).toBe('Not found')
    })

    it('returns true + removes rule + success toast on success', async () => {
      useCustomRulesStore.setState({ rules: [buildCustomRule({ id: 'rule-1' })] })

      const result = await useCustomRulesStore.getState().deleteRule('rule-1')

      expect(result).toBe(true)
      expect(useCustomRulesStore.getState().rules).toEqual([])
      const successToasts = useToastStore
        .getState()
        .toasts.filter((t) => t.variant === 'success')
      expect(successToasts).toHaveLength(1)
      expect(successToasts[0]!.title).toBe('Rule deleted')
    })
  })

  describe('toggleRule', () => {
    it('returns null + emits error toast on failure', async () => {
      server.use(
        http.post('/api/v1/meta/custom-rules/:id/toggle', () =>
          HttpResponse.json(apiError('bad'), { status: 400 }),
        ),
      )

      const result = await useCustomRulesStore.getState().toggleRule('rule-1')

      expect(result).toBeNull()
      const toasts = useToastStore.getState().toasts
      expect(toasts).toHaveLength(1)
      expect(toasts[0]!.title).toBe('Failed to toggle rule')
    })

    it('replaces rule + emits success toast on success', async () => {
      const original = buildCustomRule({ id: 'rule-1', enabled: true })
      const toggled = buildCustomRule({ id: 'rule-1', enabled: false })
      useCustomRulesStore.setState({ rules: [original] })
      server.use(
        http.post('/api/v1/meta/custom-rules/:id/toggle', () =>
          HttpResponse.json(successFor<typeof toggleCustomRuleApi>(toggled)),
        ),
      )

      const result = await useCustomRulesStore.getState().toggleRule('rule-1')

      expect(result?.enabled).toBe(false)
      expect(useCustomRulesStore.getState().rules).toEqual([toggled])
      const successToasts = useToastStore
        .getState()
        .toasts.filter((t) => t.variant === 'success')
      expect(successToasts).toHaveLength(1)
      // Store reports the resulting state, not a generic "toggled".
      expect(successToasts[0]!.title).toBe('Rule disabled')
    })
  })
})
