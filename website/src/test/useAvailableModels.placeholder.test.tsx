/**
 * The list a picker renders BEFORE `/api/models` answers is backend-aware.
 *
 * Auto is the one row that is safe to show with nothing known — but only on the
 * kiro family, which has an id for "let the server choose". claude-agent-acp
 * declares none, so a synthetic Auto during the pending window is the exact
 * option `/api/models` omits, and picking it falls into the session-reset path.
 */
import { describe, it, expect, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('../api/client', () => ({
  api: {
    // Never resolves: the hook stays in its pending state for the whole test.
    models: vi.fn(() => new Promise(() => {})),
  },
}))

import { placeholderModels, useAvailableModels } from '../hooks/useAvailableModels'

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return createElement(QueryClientProvider, { client: qc }, children)
}

describe('useAvailableModels placeholder', () => {
  it('offers Auto while a kiro-family list is pending', () => {
    const { result } = renderHook(() => useAvailableModels({ backend: '' }), { wrapper })
    expect(result.current.map(m => m.name)).toEqual(['auto'])
    expect(placeholderModels('kas').map(m => m.name)).toEqual(['auto'])
  })

  it('offers nothing while an adapted backend list is pending', () => {
    const { result } = renderHook(() => useAvailableModels({ backend: 'claude' }), { wrapper })
    expect(result.current).toEqual([])
    expect(placeholderModels('codex')).toEqual([])
  })

  it('offers nothing for an omitted backend, which may not be kiro', () => {
    // "Whatever is configured" is unknown here, so it cannot borrow kiro's row.
    const { result } = renderHook(() => useAvailableModels(), { wrapper })
    expect(result.current).toEqual([])
  })
})
