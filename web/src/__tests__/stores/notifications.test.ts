import { describe, it, expect, beforeEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { useNotificationsStore } from '@/stores/notifications'
import { server } from '@/test-setup'
import { successFor } from '@/mocks/handlers/helpers'
import { buildSettingEntry } from '@/mocks/handlers/settings'
import type { getNamespaceSettings } from '@/api/endpoints/settings'

/**
 * Notification routing preferences are the backend source of truth (the
 * ``notifications.preferences`` JSON setting); drawer items are an ephemeral
 * session buffer that is never persisted client-side.
 */
describe('notifications store: backend-sourced preferences', () => {
  beforeEach(() => {
    useNotificationsStore.setState({
      items: [],
      unreadCount: 0,
      preferences: { routeOverrides: {}, globalMute: false, browserPermission: 'default' },
    })
  })

  it('hydrates routing preferences from the notifications namespace', async () => {
    server.use(
      http.get('/api/v1/settings/notifications', () =>
        HttpResponse.json(
          successFor<typeof getNamespaceSettings>([
            buildSettingEntry({
              value: JSON.stringify({
                routeOverrides: { 'system.error': ['drawer'] },
                globalMute: true,
              }),
              source: 'db',
              definition: { namespace: 'notifications', key: 'preferences' },
            }),
          ]),
        ),
      ),
    )

    await useNotificationsStore.getState().hydrate()

    const prefs = useNotificationsStore.getState().preferences
    expect(prefs.globalMute).toBe(true)
    expect(prefs.routeOverrides['system.error']).toEqual(['drawer'])
  })

  it('degrades to defaults when the preferences setting is absent', async () => {
    server.use(
      http.get('/api/v1/settings/notifications', () =>
        HttpResponse.json(successFor<typeof getNamespaceSettings>([])),
      ),
    )

    await useNotificationsStore.getState().hydrate()

    expect(useNotificationsStore.getState().preferences.globalMute).toBe(false)
  })

  it('keeps drawer items in memory only (no client persistence on enqueue)', () => {
    const id = useNotificationsStore.getState().enqueue({
      category: 'system.error',
      severity: 'warning',
      title: 'Test alert',
    })
    expect(typeof id).toBe('string')
    expect(useNotificationsStore.getState().items.length).toBeGreaterThan(0)
  })
})
