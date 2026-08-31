import { describe, expect, it, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import {
  markModelsDegraded,
  modelsDegraded,
  useModelsDegraded,
} from '../providers/modelListHealth'

describe('useModelsDegraded', () => {
  beforeEach(() => {
    // The store is module-level; reset every provider+backend pair used below.
    markModelsDegraded('acp', undefined, false)
    markModelsDegraded('other', undefined, false)
    markModelsDegraded('acp', '', false)
    markModelsDegraded('acp', 'codex', false)
  })

  it('re-renders when the flag flips with no change to the model list', () => {
    // The scenario the plain getter cannot serve: a failed /api/models resolves
    // SUCCESSFULLY with the last-good cached list, so React Query hands back a
    // structurally identical result and notifies nobody. Only a subscription
    // sees the flag move, and the composer's displayed model depends on it.
    const { result } = renderHook(() => useModelsDegraded('acp'))
    expect(result.current).toBe(false)

    act(() => {
      markModelsDegraded('acp', undefined, true)
    })
    expect(result.current).toBe(true)

    act(() => {
      markModelsDegraded('acp', undefined, false)
    })
    expect(result.current).toBe(false)
  })

  it('is provider-scoped', () => {
    const { result } = renderHook(() => useModelsDegraded('acp'))
    act(() => {
      markModelsDegraded('other', undefined, true)
    })
    expect(result.current).toBe(false)
  })

  it('reports false for a provider that never fetched', () => {
    const { result } = renderHook(() => useModelsDegraded('never-seen'))
    expect(result.current).toBe(false)
  })

  it('is backend-scoped within one provider', () => {
    // Every backend runs through the same `acp` provider, so a flag keyed on the
    // provider alone is one flag for all of them. A Kiro success would then stop
    // the self-heal poll for a Codex picker still serving a stale cached list,
    // and a Codex failure would keep a healthy Kiro picker polling forever.
    const kiro = renderHook(() => useModelsDegraded('acp', ''))
    const codex = renderHook(() => useModelsDegraded('acp', 'codex'))

    act(() => {
      markModelsDegraded('acp', 'codex', true)
    })
    expect(codex.result.current).toBe(true)
    expect(kiro.result.current).toBe(false)

    act(() => {
      markModelsDegraded('acp', '', true)
      markModelsDegraded('acp', 'codex', false)
    })
    expect(kiro.result.current).toBe(true)
    expect(codex.result.current).toBe(false)
  })

  it('separates an omitted backend from an explicit kiro backend', () => {
    // `undefined` asks about the CONFIGURED backend and `''` asks about kiro;
    // they are distinct query keys, so they must be distinct health entries.
    const configured = renderHook(() => useModelsDegraded('acp'))
    const explicitKiro = renderHook(() => useModelsDegraded('acp', ''))

    act(() => {
      markModelsDegraded('acp', '', true)
    })
    expect(explicitKiro.result.current).toBe(true)
    expect(configured.result.current).toBe(false)
  })

  it('agrees with the non-reactive getter', () => {
    // The refetch-cadence path still reads the getter, so the two must not
    // diverge.
    const { result } = renderHook(() => useModelsDegraded('acp'))
    act(() => {
      markModelsDegraded('acp', undefined, true)
    })
    expect(result.current).toBe(modelsDegraded('acp'))
  })
})
