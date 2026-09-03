/**
 * One helper answers "which harness is this slot on", because the three chat
 * surfaces that used to answer it independently could disagree — and the
 * disagreement is silent: the loser renders another harness's model vocabulary
 * next to a session that cannot run any of it.
 *
 * The trap the helper exists to close is `||`. Kiro's id IS the empty string,
 * so a truthiness coalesce reads a slot deliberately bound to kiro as never
 * bound, and hands it whatever the configured default happens to say.
 */
import { describe, it, expect } from 'vitest'

import {
  ACP_BACKEND_CLAUDE,
  ACP_BACKEND_CODEX,
  ACP_BACKEND_KIRO,
  ACP_BACKEND_OPTIONS,
  acpBackendLabel,
  resolveSlotBackend,
} from '../providers/acpBackends'

describe('resolveSlotBackend', () => {
  it('prefers the slot\'s own binding over the configured default', () => {
    expect(resolveSlotBackend(ACP_BACKEND_CODEX, ACP_BACKEND_CLAUDE)).toBe(ACP_BACKEND_CODEX)
  })

  it('treats an explicit kiro binding as a binding, not as unset', () => {
    expect(resolveSlotBackend(ACP_BACKEND_KIRO, ACP_BACKEND_CODEX)).toBe(ACP_BACKEND_KIRO)
  })

  it('falls back to the configured default only when the slot has none', () => {
    expect(resolveSlotBackend(undefined, ACP_BACKEND_CODEX)).toBe(ACP_BACKEND_CODEX)
    // The slots payload spells "no binding" as null as well as absent.
    expect(resolveSlotBackend(null, ACP_BACKEND_CLAUDE)).toBe(ACP_BACKEND_CLAUDE)
  })

  it('lands on kiro when neither side has an answer', () => {
    // Kiro is the floor: an unconfigured, unbound slot is created on it.
    expect(resolveSlotBackend(undefined, undefined)).toBe(ACP_BACKEND_KIRO)
  })
})

describe('acpBackendLabel', () => {
  it('names every offered harness', () => {
    expect(ACP_BACKEND_OPTIONS.map(acpBackendLabel))
      .toEqual(['Kiro CLI', 'Claude Code', 'Codex'])
  })

  it('names a harness the dashboard does not offer rather than rendering a blank', () => {
    // A config written by hand can persist one. The control still has to say
    // where the slot is before it can offer to move it off.
    expect(acpBackendLabel('kas')).toBe('External backend (kas)')
  })
})
