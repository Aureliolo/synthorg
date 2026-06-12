import { http, HttpResponse } from 'msw'
import type {
  exportSecurityConfig,
  getAllSettings,
  getNamespaceSchema,
  getNamespaceSettings,
  getSchema,
  importSecurityConfig,
  listSinks,
  testSinkConfig,
  updateSetting,
} from '@/api/endpoints/settings'
import type { SecurityConfigImportRequest } from '@/api/types/settings'
import type { SettingEntry } from '@/api/types/settings'
import {
  emptyPage,
  paginatedEnvelopeFor,
  paginatedFor,
  successFor,
  voidSuccess,
} from './helpers'

type SettingEntryOverrides = Partial<Omit<SettingEntry, 'definition'>> & {
  definition?: Partial<SettingEntry['definition']>
}

export function buildSettingEntry(
  overrides: SettingEntryOverrides = {},
): SettingEntry {
  const base: SettingEntry = {
    definition: {
      namespace: 'api',
      key: 'default-key',
      type: 'str',
      default: null,
      description: '',
      group: 'default',
      level: 'basic',
      sensitive: false,
      restart_required: false,
      read_only_post_init: false,
      env_var_override: null,
      enum_values: [],
      validator_pattern: null,
      min_value: null,
      max_value: null,
    },
    value: '',
    source: 'default',
    updated_at: null,
  }
  return {
    ...base,
    ...overrides,
    definition: { ...base.definition, ...overrides.definition },
  }
}

export const settingsHandlers = [
  http.get('/api/v1/settings/_schema', () =>
    HttpResponse.json(successFor<typeof getSchema>([])),
  ),
  http.get('/api/v1/settings/_schema/:namespace', () =>
    HttpResponse.json(successFor<typeof getNamespaceSchema>([])),
  ),
  http.get('/api/v1/settings', () =>
    HttpResponse.json(
      paginatedFor<typeof getAllSettings>(emptyPage<SettingEntry>()),
    ),
  ),
  http.get('/api/v1/settings/observability/sinks', () =>
    HttpResponse.json(paginatedEnvelopeFor<typeof listSinks>()),
  ),
  http.post('/api/v1/settings/observability/sinks/_test', async ({ request }) => {
    await request.json()
    return HttpResponse.json(
      successFor<typeof testSinkConfig>({ valid: true, error: null }),
    )
  }),
  http.get('/api/v1/settings/:namespace', () =>
    HttpResponse.json(successFor<typeof getNamespaceSettings>([])),
  ),
  http.put('/api/v1/settings/:namespace/:key', async ({ params, request }) => {
    const body = (await request.json()) as { value: string }
    return HttpResponse.json(
      successFor<typeof updateSetting>(
        buildSettingEntry({
          value: body.value,
          source: 'db',
          updated_at: '2026-04-19T00:00:00Z',
          definition: {
            namespace: String(
              params['namespace'],
            ) as SettingEntry['definition']['namespace'],
            key: String(params['key']),
          },
        }),
      ),
    )
  }),
  http.delete('/api/v1/settings/:namespace/:key', () =>
    HttpResponse.json(voidSuccess()),
  ),
  http.get('/api/v1/settings/security/export', () =>
    HttpResponse.json(
      successFor<typeof exportSecurityConfig>({
        config: { enabled: true, audit_enabled: true },
        exported_at: '2026-04-19T00:00:00Z',
        custom_policies_warning: null,
      }),
    ),
  ),
  http.post('/api/v1/settings/security/import', async ({ request }) => {
    const body = (await request.json()) as SecurityConfigImportRequest
    return HttpResponse.json(
      successFor<typeof importSecurityConfig>({
        config: body.config,
        exported_at: '2026-04-19T00:00:00Z',
        custom_policies_warning: null,
      }),
    )
  }),
]
