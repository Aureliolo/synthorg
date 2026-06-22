import type { TaskStatus } from '@/api/types/enums'
import { HIDDEN_SETTINGS, NAMESPACE_DISPLAY_NAMES, NAMESPACE_ORDER } from '@/pages/settings/settings-constants'
import {
  DEFAULT_PAGE_SIZE,
  LOGIN_LOCKOUT_MS,
  LOGIN_MAX_ATTEMPTS,
  MAX_PAGE_SIZE,
  MIN_PASSWORD_LENGTH,
} from '@/utils/constants'
import {
  TASK_STATUS_ORDER,
  TERMINAL_STATUSES,
  VALID_TRANSITIONS,
  WRITE_ROLES,
} from '@/utils/tasks'
import {
  WS_MAX_MESSAGE_SIZE,
  WS_MAX_RECONNECT_ATTEMPTS,
  WS_RECONNECT_BASE_DELAY,
  WS_RECONNECT_MAX_DELAY,
} from '@/utils/ws-constants'

describe('constants', () => {
  describe('WebSocket constants', () => {
    it('has sane reconnect defaults', () => {
      expect(WS_RECONNECT_BASE_DELAY).toBe(1000)
      expect(WS_RECONNECT_MAX_DELAY).toBe(30000)
      expect(WS_MAX_RECONNECT_ATTEMPTS).toBe(20)
      // 32 KiB matches the server's _MAX_OUTBOUND_EVENT_BYTES; the
      // pre-WEB-1 value of 128 KiB was 4x larger than any realistic
      // event payload and decoupled from the server-side cap.
      expect(WS_MAX_MESSAGE_SIZE).toBe(32_768)
    })

    /**
     * Cross-language coordination lock: the version + heartbeat values
     * are duplicated between this file and `src/synthorg/api/`. If
     * someone bumps either side without the other, this test fails
     * loudly and forces a coordinated change. Backend defaults live at:
     *   - WsEvent.version default (src/synthorg/api/ws_models.py)
     *   - heartbeat tolerance (the server has no explicit timeout but
     *     the channels plugin's send window is well above 30s)
     */
    it('locks heartbeat + protocol version constants', async () => {
      const { WS_HEARTBEAT_INTERVAL_MS, WS_PONG_TIMEOUT_MS, WS_PROTOCOL_VERSION } =
        await import('@/utils/ws-constants')
      expect(WS_HEARTBEAT_INTERVAL_MS).toBe(20_000)
      expect(WS_PONG_TIMEOUT_MS).toBe(10_000)
      expect(WS_PROTOCOL_VERSION).toBe(1)
    })
  })

  describe('pagination constants', () => {
    it('has sane page size defaults', () => {
      expect(DEFAULT_PAGE_SIZE).toBeLessThanOrEqual(MAX_PAGE_SIZE)
      expect(DEFAULT_PAGE_SIZE).toBeGreaterThan(0)
    })
  })

  describe('login constants', () => {
    it('has sane login lockout defaults', () => {
      expect(LOGIN_MAX_ATTEMPTS).toBeGreaterThan(0)
      expect(LOGIN_LOCKOUT_MS).toBeGreaterThan(0)
      expect(MIN_PASSWORD_LENGTH).toBeGreaterThanOrEqual(8)
    })
  })

  describe('WRITE_ROLES', () => {
    it('contains expected roles', () => {
      expect(WRITE_ROLES).toContain('ceo')
      expect(WRITE_ROLES).toContain('manager')
      expect(WRITE_ROLES).toContain('pair_programmer')
      expect(WRITE_ROLES).not.toContain('board_member')
      expect(WRITE_ROLES).not.toContain('observer')
      expect(WRITE_ROLES).not.toContain('system')
      expect(WRITE_ROLES).toHaveLength(3)
    })
  })

  describe('TASK_STATUS_ORDER', () => {
    it('contains all statuses from VALID_TRANSITIONS', () => {
      const transitionKeys = Object.keys(VALID_TRANSITIONS) as TaskStatus[]
      for (const status of transitionKeys) {
        expect(TASK_STATUS_ORDER).toContain(status)
      }
    })

    it('has no duplicates', () => {
      const unique = new Set(TASK_STATUS_ORDER)
      expect(unique.size).toBe(TASK_STATUS_ORDER.length)
    })
  })

  describe('VALID_TRANSITIONS', () => {
    it('terminal statuses have no transitions', () => {
      for (const status of TERMINAL_STATUSES) {
        expect(VALID_TRANSITIONS[status]).toHaveLength(0)
      }
    })

    it('all transition targets are valid statuses', () => {
      const allStatuses = new Set(TASK_STATUS_ORDER)
      for (const targets of Object.values(VALID_TRANSITIONS)) {
        for (const target of targets) {
          expect(allStatuses.has(target)).toBe(true)
        }
      }
    })

    it('non-terminal statuses have at least one transition', () => {
      for (const [status, targets] of Object.entries(VALID_TRANSITIONS)) {
        if (!TERMINAL_STATUSES.has(status as TaskStatus)) {
          expect(targets.length).toBeGreaterThan(0)
        }
      }
    })
  })

  describe('NAMESPACE_ORDER', () => {
    it('surfaces company (Org Edit only covers the company REST API)', () => {
      // The dedicated Org Edit page covers name / autonomy / budget via the
      // company REST API; the registry-only keys (description, name_locales)
      // are only reachable through the generic panel, so company is included.
      expect(NAMESPACE_ORDER).toContain('company')
    })

    it('excludes providers (the dedicated Providers page covers every key)', () => {
      expect(NAMESPACE_ORDER).not.toContain('providers')
    })

    it('hides the structural company JSON blobs from the generic panel', () => {
      expect(HIDDEN_SETTINGS.has('company/agents')).toBe(true)
      expect(HIDDEN_SETTINGS.has('company/departments')).toBe(true)
    })

    it('every namespace in order has a display name', () => {
      for (const ns of NAMESPACE_ORDER) {
        expect(NAMESPACE_DISPLAY_NAMES[ns]).toBeDefined()
      }
    })
  })
})
