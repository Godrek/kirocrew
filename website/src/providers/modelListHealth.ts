// Tracks whether the model list currently served for a given provider+backend
// came from a LIVE /api/models success or from a DEGRADED fallback (cached list
// or auto-only). The model-picker query polls until a live fetch succeeds, so
// the self-heal decision must key off this explicit signal — NOT the shape/length
// of the list, which cannot distinguish a live single-model backend from a
// degraded fallback, and would stop polling the moment a
// possibly-stale cached multi-entry list is served.
//
// Keyed by the SAME pair the query is keyed by — `['available-models',
// <providerId>, <backend ?? null>]` — because the backends' vocabularies are
// disjoint and so are their failures. One flag shared across backends lets a
// Kiro success stop polling a Codex query that is still serving a stale cached
// list, and a Codex failure keep a healthy Kiro picker polling forever. Default
// is "not degraded" (undefined → false): a provider whose adapter never marks
// itself only ever stops polling, so this can never regress an unmarked provider
// into perpetual polling.
import { useSyncExternalStore } from 'react'

/** The health-map key for one picker.
 *
 *  Built from the query key's own components so the two cannot drift: `undefined`
 *  (ask about the CONFIGURED backend) and `''` (ask about kiro explicitly) are
 *  distinct query keys and must stay distinct here. */
function healthKey(providerId: string, backend: string | undefined): string {
  return JSON.stringify([providerId, backend ?? null])
}

const degradedByKey = new Map<string, boolean>()
const subscribers = new Set<() => void>()

/** Record whether the last fetch for a provider+backend was degraded (fallback)
 *  or live. The adapter calls this on every fetch outcome. */
export function markModelsDegraded(
  providerId: string,
  backend: string | undefined,
  degraded: boolean,
): void {
  const key = healthKey(providerId, backend)
  if (degradedByKey.get(key) === degraded) return
  degradedByKey.set(key, degraded)
  for (const cb of subscribers) cb()
}

function subscribe(cb: () => void): () => void {
  subscribers.add(cb)
  return () => {
    subscribers.delete(cb)
  }
}

/** True only when the provider+backend's last served list is known to be a
 *  degraded fallback. Unknown/never-fetched pairs report false (not degraded). */
export function modelsDegraded(providerId: string, backend?: string): boolean {
  return degradedByKey.get(healthKey(providerId, backend)) === true
}

/**
 * Reactive form of `modelsDegraded`, for components that RENDER something from
 * the flag rather than just deciding a refetch cadence.
 *
 * A plain call cannot be read during render: a failed fetch resolves
 * SUCCESSFULLY with the last-good cached list, so when that list is
 * structurally identical to the one React Query already holds it hands back the
 * same reference and notifies nobody. The flag flips with no re-render, and the
 * component keeps rendering the previous decision until something unrelated
 * happens to re-render it.
 */
export function useModelsDegraded(providerId: string, backend?: string): boolean {
  return useSyncExternalStore(
    subscribe,
    () => modelsDegraded(providerId, backend),
    () => false,
  )
}

/**
 * refetch cadence for the ['available-models', <providerId>, <backend>] query:
 * poll every 8s WHILE the served list is a degraded fallback, then stop the
 * instant a LIVE fetch succeeds. Reads both key components from the query key
 * itself, so it is decoupled from the list shape and cannot be pointed at a
 * different backend's health than the one it is pacing. RQ v5 passes the Query
 * instance.
 */
export function modelListRefetchInterval(
  query: { queryKey: readonly unknown[] },
): number | false {
  const providerId = typeof query.queryKey[1] === 'string' ? query.queryKey[1] : ''
  // The key stores `backend ?? null`; anything else (including a missing third
  // element on a legacy two-part key) reads as "the configured backend".
  const raw = query.queryKey[2]
  const backend = typeof raw === 'string' ? raw : undefined
  return modelsDegraded(providerId, backend) ? 8_000 : false
}
