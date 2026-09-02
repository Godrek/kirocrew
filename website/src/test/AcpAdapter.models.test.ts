import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock the API client so fetchAvailableModels reads our canned /api/models.
vi.mock('../api/client', () => ({
  api: {
    models: vi.fn(),
  },
}))

import { api } from '../api/client'
import { AcpAdapter } from '../providers/adapters/acp'
import {
  markModelsDegraded,
  modelsDegraded,
  modelListRefetchInterval,
} from '../providers/modelListHealth'

/** The kiro backend's cache entry.
 *
 *  The key is scoped PER BACKEND (`kc.acp.models.v2.<backend>`, with the kiro
 *  backend's empty id spelled `kiro` so an empty segment cannot look like a
 *  missing one). One shared entry would let the last backend to fetch decide
 *  what every picker serves from cache on the next cold start. */
const KIRO_CACHE_KEY = 'kc.acp.models.v2.kiro'
const CODEX_CACHE_KEY = 'kc.acp.models.v2.codex'
/** The entry for a caller that named no backend — "whatever is configured".
 *
 *  Distinct from kiro's: folding the two would make an omitted argument mean
 *  "kiro", which is the one thing it must not mean on a non-kiro dashboard. */
const CONFIGURED_CACHE_KEY = 'kc.acp.models.v2.configured'

/** Read one backend's persisted entry. Typed so the per-backend assertions do
 *  not need a cast — the shape is this adapter's own, not remote input. */
function readCache(key: string): { ts: number; models: { name: string }[] } {
  return JSON.parse(localStorage.getItem(key) as string)
}

describe('AcpAdapter.fetchAvailableModels', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('returns backend-advertised models on success', async () => {
    ;(api.models as any).mockResolvedValue([
      { model_name: 'auto', description: 'Let the provider pick' },
      { model_name: 'claude-opus-4.8', description: 'Most capable' },
      { model_name: 'claude-sonnet-4.6', description: 'Everyday tasks' },
    ])
    const models = await new AcpAdapter().fetchAvailableModels('')
    expect(models.length).toBe(3)
    expect(models[0].name).toBe('auto')
    expect(models[1].name).toBe('claude-opus-4.8')
    expect(models[2].description).toBe('Everyday tasks')
  })

  it('falls back to AUTO-ONLY when API returns non-array (e.g. error object)', async () => {
    ;(api.models as any).mockResolvedValue({ error: 'Token required' })
    const models = await new AcpAdapter().fetchAvailableModels('')
    // Never surface canonical registry keys (opus-4.8-1m, fable-5-1m, …): the
    // ACP CLI rejects them as model ids (-32603). Only 'auto' is safe.
    expect(models).toHaveLength(1)
    expect(models[0].name).toBe('auto')
    expect(models.some(m => m.name.includes('-1m') || m.name === 'opus-4.8')).toBe(false)
  })

  it('treats an empty success as authoritative: no options, not degraded', async () => {
    // `/api/models` answers 200 `[]` only for a backend with no vocabulary to
    // report (every listing failure is a 503, which the catch path handles).
    // Reading it as degraded would poll every 8s forever and serve a fallback
    // in place of the truthful "nothing to offer".
    markModelsDegraded('acp', 'codex', true) // a prior failure, since healed
    vi.mocked(api.models).mockResolvedValue([])
    const models = await new AcpAdapter().fetchAvailableModels('codex')
    expect(models).toEqual([])
    expect(modelsDegraded('acp', 'codex')).toBe(false)
    expect(modelListRefetchInterval({ queryKey: ['available-models', 'acp', 'codex'] })).toBe(false)
  })

  it('does not fall back to auto on an empty success, even for kiro', async () => {
    // The kiro path never answers 200 `[]` — its failures are all 503s — so an
    // empty success here is the server's authoritative answer, and inventing
    // an Auto row over it would contradict it.
    vi.mocked(api.models).mockResolvedValue([])
    expect(await new AcpAdapter().fetchAvailableModels('')).toEqual([])
    expect(modelsDegraded('acp', '')).toBe(false)
  })

  it('falls back to AUTO-ONLY when API throws (timeout, network error)', async () => {
    ;(api.models as any).mockRejectedValue(new Error('fetch timeout'))
    const models = await new AcpAdapter().fetchAvailableModels('')
    expect(models).toHaveLength(1)
    expect(models[0].name).toBe('auto')
  })

  it('auto-only fallback carries a sensible context window', async () => {
    ;(api.models as any).mockRejectedValue(new Error('boom'))
    const models = await new AcpAdapter().fetchAvailableModels('')
    expect(models[0].name).toBe('auto')
    expect(models[0].contextWindow).toBeGreaterThan(0)
  })

  it('persists a good live list to localStorage', async () => {
    ;(api.models as any).mockResolvedValue([
      { model_name: 'auto', description: 'a' },
      { model_name: 'claude-opus-4.8', description: 'b' },
    ])
    await new AcpAdapter().fetchAvailableModels('')
    const raw = localStorage.getItem(KIRO_CACHE_KEY)
    expect(raw).toBeTruthy()
    const cached = JSON.parse(raw as string)
    expect(cached.models.map((m: any) => m.name)).toEqual(['auto', 'claude-opus-4.8'])
    expect(typeof cached.ts).toBe('number')
  })

  it('serves the last-good cached list (not auto-only) when the API throws', async () => {
    // Prime the cache with a good live fetch.
    ;(api.models as any).mockResolvedValueOnce([
      { model_name: 'auto', description: 'a' },
      { model_name: 'claude-opus-4.8', description: 'b' },
      { model_name: 'claude-fable-5', description: 'c' },
    ])
    const adapter = new AcpAdapter()
    await adapter.fetchAvailableModels()
    // Next fetch fails transiently — should degrade to the cached 3, not auto-only.
    ;(api.models as any).mockRejectedValue(new Error('503'))
    const models = await adapter.fetchAvailableModels()
    expect(models).toHaveLength(3)
    expect(models.map(m => m.name)).toContain('claude-fable-5')
  })

  it('falls back to auto-only when the API throws and there is no cache', async () => {
    ;(api.models as any).mockRejectedValue(new Error('503'))
    const models = await new AcpAdapter().fetchAvailableModels('')
    expect(models).toHaveLength(1)
    expect(models[0].name).toBe('auto')
  })

  it('offers nothing when the backend is unknown and the fetch fails', async () => {
    // An OMITTED backend means "whatever is configured", which the adapter
    // cannot resolve. `auto` is a kiro-family sentinel, not a universal one, so
    // offering it here hands a Claude or Codex dashboard the one cross-backend
    // option this module exists to keep out — and on those harnesses picking it
    // resets the session. No options is the truthful degradation; the tradeoff
    // is a kiro deployment briefly showing an empty picker during a transient
    // failure, which the per-backend cache covers in the common case.
    vi.mocked(api.models).mockRejectedValue(new Error('503'))
    expect(await new AcpAdapter().fetchAvailableModels()).toEqual([])
    expect(await new AcpAdapter().fetchAvailableModels('claude')).toEqual([])
  })

  it('does not overwrite the cache with an empty/failed result', async () => {
    ;(api.models as any).mockResolvedValueOnce([
      { model_name: 'auto', description: 'a' },
      { model_name: 'claude-opus-4.8', description: 'b' },
    ])
    const adapter = new AcpAdapter()
    await adapter.fetchAvailableModels('')
    ;(api.models as any).mockResolvedValue([]) // empty success must not clobber cache
    await adapter.fetchAvailableModels('')
    const cached = JSON.parse(localStorage.getItem(KIRO_CACHE_KEY) as string)
    expect(cached.models).toHaveLength(2)
    vi.mocked(api.models).mockRejectedValue(new Error('503')) // nor a failure
    await adapter.fetchAvailableModels('')
    expect(JSON.parse(localStorage.getItem(KIRO_CACHE_KEY) as string).models).toHaveLength(2)
  })

  it('ignores a cache older than the TTL (bounds -32603 exposure)', async () => {
    // Write a stale cache (25h old) directly.
    localStorage.setItem(
      KIRO_CACHE_KEY,
      JSON.stringify({
        ts: Date.now() - 25 * 60 * 60 * 1000,
        models: [{ name: 'auto' }, { name: 'stale-model' }],
      }),
    )
    ;(api.models as any).mockRejectedValue(new Error('503'))
    const models = await new AcpAdapter().fetchAvailableModels('')
    // Too stale to trust → auto-only, not the stale cached list.
    expect(models).toHaveLength(1)
    expect(models[0].name).toBe('auto')
  })

  it('caches each backend separately so one cannot serve another', async () => {
    // The regression the per-backend key exists for: a Codex list served to a
    // Kiro picker on the next cold start is a dropdown of ids the kiro wire
    // rejects.
    const adapter = new AcpAdapter()
    vi.mocked(api.models).mockResolvedValueOnce([
      { model_name: 'claude-opus-4.8', description: '' },
    ])
    await adapter.fetchAvailableModels('')
    vi.mocked(api.models).mockResolvedValueOnce([{ model_name: 'gpt-5.6-codex', description: '' }])
    await adapter.fetchAvailableModels('codex')

    const kiro = readCache(KIRO_CACHE_KEY)
    const codex = readCache(CODEX_CACHE_KEY)
    expect(kiro.models.map(m => m.name)).toEqual(['claude-opus-4.8'])
    expect(codex.models.map(m => m.name)).toEqual(['gpt-5.6-codex'])
  })

  it('serves the degraded fallback from the SAME backend cache', async () => {
    const adapter = new AcpAdapter()
    vi.mocked(api.models).mockResolvedValueOnce([
      { model_name: 'claude-opus-4.8', description: '' },
    ])
    await adapter.fetchAvailableModels('')
    // Codex has no cache of its own, so a failure there must NOT borrow kiro's
    // entry — and must not fall back to "auto" either. "auto" is a kiro-family
    // sentinel, not a universal one: a backend that declares no id for it would
    // be handed the single row whose only outcome is a session reset. No
    // choices is the truthful degradation.
    vi.mocked(api.models).mockRejectedValue(new Error('503'))
    const codexModels = await adapter.fetchAvailableModels('codex')
    expect(codexModels).toEqual([])
  })

  it('still degrades to auto for the kiro family, which serves it', async () => {
    // The other half of the rule: kiro DOES advertise an `auto` id, so losing
    // the picker entirely during a gateway restart would be a regression on the
    // floor harness.
    vi.mocked(api.models).mockRejectedValue(new Error('503'))
    const models = await new AcpAdapter().fetchAvailableModels('')
    expect(models.map(m => m.name)).toEqual(['auto'])
  })

  it('does not treat an OMITTED backend as the kiro backend', async () => {
    // Omitting `backend` asks about the CONFIGURED harness, which on a Claude
    // dashboard is not kiro. A default parameter of '' here would answer that
    // question with kiro's list and cache it under kiro's key — so legacy
    // callers (Research Lab, Agents, Knowledge) would render Kiro models to a
    // user running Claude, and the two would then share one cache entry.
    const adapter = new AcpAdapter()
    vi.mocked(api.models).mockResolvedValueOnce([
      { model_name: 'opus-4.8-1m', description: '' },
    ])
    await adapter.fetchAvailableModels()

    expect(vi.mocked(api.models)).toHaveBeenCalledWith(undefined)
    expect(localStorage.getItem(KIRO_CACHE_KEY)).toBeNull()
    expect(readCache(CONFIGURED_CACHE_KEY).models.map(m => m.name)).toEqual(['opus-4.8-1m'])
  })

  it('ignores a cache with a future timestamp (clock skew)', async () => {
    localStorage.setItem(
      KIRO_CACHE_KEY,
      JSON.stringify({
        ts: Date.now() + 60 * 60 * 1000, // 1h in the future
        models: [{ name: 'auto' }, { name: 'skewed-model' }],
      }),
    )
    ;(api.models as any).mockRejectedValue(new Error('503'))
    const models = await new AcpAdapter().fetchAvailableModels('')
    expect(models).toHaveLength(1)
    expect(models[0].name).toBe('auto')
  })
})

describe('model-list liveness (self-heal signal)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    markModelsDegraded('acp', undefined, false)
  })

  it('marks degraded on failure and clears it on a live success', async () => {
    ;(api.models as any).mockRejectedValue(new Error('503'))
    const adapter = new AcpAdapter()
    await adapter.fetchAvailableModels()
    expect(modelsDegraded('acp')).toBe(true)
    // Poll continues while degraded, regardless of served list length.
    expect(modelListRefetchInterval({ queryKey: ['available-models', 'acp'] })).toBe(8_000)

    ;(api.models as any).mockResolvedValue([
      { model_name: 'auto', description: 'a' },
      { model_name: 'claude-opus-4.8', description: 'b' },
    ])
    await adapter.fetchAvailableModels()
    expect(modelsDegraded('acp')).toBe(false)
    // Live success → stop polling.
    expect(modelListRefetchInterval({ queryKey: ['available-models', 'acp'] })).toBe(false)
  })

  it('keeps polling on a degraded CACHED multi-model list (the -32603/stale bug)', async () => {
    // Prime a good live list, then fail: the served list is multi-entry but
    // degraded — polling MUST continue.
    ;(api.models as any).mockResolvedValueOnce([
      { model_name: 'auto', description: 'a' },
      { model_name: 'claude-opus-4.8', description: 'b' },
      { model_name: 'claude-fable-5', description: 'c' },
    ])
    const adapter = new AcpAdapter()
    await adapter.fetchAvailableModels()
    ;(api.models as any).mockRejectedValue(new Error('503'))
    const served = await adapter.fetchAvailableModels()
    expect(served.length).toBeGreaterThan(1) // multi-entry cached list
    expect(modelsDegraded('acp')).toBe(true)
    expect(modelListRefetchInterval({ queryKey: ['available-models', 'acp'] })).toBe(8_000)
  })

  it('does not poll an unmarked/unknown provider', () => {
    expect(modelListRefetchInterval({ queryKey: ['available-models', 'other'] })).toBe(false)
  })
})
