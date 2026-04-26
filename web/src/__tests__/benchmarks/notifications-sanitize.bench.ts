/**
 * CodSpeed bench for `sanitizeWsString()`.
 *
 * Runs on every string field of every WebSocket payload (notifications,
 * messages, approvals, tasks, agents). C0 control-char strip + bidi-
 * override removal + length cap @ 128. A regression here scales with
 * dashboard-wide WS event volume.
 */
import { bench, describe } from 'vitest'

// Imports the pure helper directly (not from the Zustand store) so
// the bench measures only the sanitization cost -- no toast queue,
// no persistence subscription, no localStorage hydration.
import { sanitizeWsString } from '@/utils/ws-sanitize'

const CLEAN_PAYLOAD = 'Task task-abc-0042 completed by agent backend-7 in 1.2s'
const CONTROL_CHAR_HEAVY = '\x00\x01\x02foo\x03\x04bar\x05\x06baz\x07\x08\x0b\x0c'
// U+202E RIGHT-TO-LEFT OVERRIDE + U+202C POP DIRECTIONAL FORMATTING
// are the Trojan Source-attack characters (CVE-2021-42574 class)
// that ``sanitizeWsString`` must strip from inbound WS payloads.
// We assemble the bench input via ``String.fromCharCode`` so the
// source file itself contains zero bidi characters: ESLint's
// ``security/detect-bidi-characters`` rule scans the source, not
// the runtime value, so this avoids the lint warning while still
// exercising the bidi-strip code path at runtime. Disabling the
// lint rule per-line is not a viable alternative -- we genuinely
// need bidi characters in the bench payload to measure the
// sanitiser's performance against real attack input.
const RLO = String.fromCharCode(0x202e)
const PDF = String.fromCharCode(0x202c)
const BIDI_ATTACK = `innocent${RLO}text${RLO}otherwise hidden${PDF}`
const LONG = 'a'.repeat(500) + ' tail content past the 128-codepoint cap'

describe('sanitizeWsString', () => {
  bench('clean payload x500', () => {
    for (let i = 0; i < 500; i++) {
      sanitizeWsString(CLEAN_PAYLOAD)
    }
  })

  bench('control-char heavy x500', () => {
    for (let i = 0; i < 500; i++) {
      sanitizeWsString(CONTROL_CHAR_HEAVY)
    }
  })

  bench('bidi-attack payload x500', () => {
    for (let i = 0; i < 500; i++) {
      sanitizeWsString(BIDI_ATTACK)
    }
  })

  bench('over-length payload x500 (length cap path)', () => {
    for (let i = 0; i < 500; i++) {
      sanitizeWsString(LONG)
    }
  })
})
