import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import { coldStartCapabilities, type ModelCapabilities } from '../providers/acpBackends'

/**
 * What the ACP backend behind THIS surface can do about models.
 *
 * Every model affordance reads it: whether to render a picker at all, whether a
 * pick takes effect now or on the next session, and whether the harness has a
 * reasoning-effort control. The alternative — deciding from the backend's
 * identity in the component — is how a control ends up hidden from a harness
 * that supports it, or offered to one that does not, with the rule spread across
 * five files that drift apart.
 *
 * Pass `slot` for a chat surface: the server resolves a bound slot to its LIVE
 * backend, so an existing session keeps describing itself correctly after the
 * operator changes the default for new sessions, and an unbound slot falls
 * through to the configured backend it will be created on. Pass `backend` for
 * Settings, which asks about a harness that may have no session at all.
 *
 * Until the first response arrives — and if the endpoint is unreachable at all,
 * which is what a version-skewed gateway serving an older API to a newer bundle
 * looks like — the answer comes from `coldStartCapabilities(coldStartBackend)`.
 * That is asymmetric on purpose: kiro gets its real (statically-known) shape so
 * the model chip never disappears, every adapted harness gets the conservative
 * one. Pass `coldStartBackend` as the backend this surface is BOUND to (falling
 * back to the configured default only for a slot that has not started), not
 * the configured default alone, or a live Codex pane will assume kiro's
 * controls for a frame.
 *
 * `coldStartBackend` is also part of the cache KEY. A slot-keyed answer
 * describes whichever backend the server resolved the slot to at the time, and
 * that resolution moves: the slot binds, unbinds, or the configured default
 * changes underneath an unbound slot. Queries here never go stale on their own,
 * and only session spawn invalidates them, so a key that ignores the backend
 * keeps the old harness's payload — a picker hidden after a rebind, or the
 * wrong switch scope — indefinitely. With the backend in the key, each of those
 * transitions is a different question with its own entry.
 */
/** The cache key for one capability question, shared with readers that look
 *  the answer up outside a render (the keyboard model cycle in `App`). The
 *  fourth member is the backend the caller believes it is asking about; a
 *  reader must derive it the same way the surface did — the slot's bound
 *  backend, else the configured default — or it looks up an entry nobody wrote. */
export function modelCapabilitiesKey(
  { slot, backend, coldStartBackend }: {
    slot?: string
    backend?: string
    coldStartBackend?: string
  },
): readonly [string, string | null, string | null, string | null] {
  return ['model-capabilities', slot ?? null, backend ?? null, coldStartBackend ?? backend ?? null]
}

export function useModelCapabilities(
  { slot, backend, enabled, coldStartBackend }: {
    slot?: string
    backend?: string
    enabled?: boolean
    coldStartBackend?: string
  } = {},
): ModelCapabilities {
  const { data } = useQuery({
    // `null` rather than `undefined` in the key: React Query serializes keys,
    // and an undefined member is not distinguishable from an absent one, so the
    // "configured backend" question would share an entry with a slot's.
    queryKey: modelCapabilitiesKey({ slot, backend, coldStartBackend }),
    queryFn: () => api.modelCapabilities({ slot, backend }),
    ...(enabled === undefined ? {} : { enabled }),
  })
  return data ?? coldStartCapabilities(coldStartBackend ?? backend)
}
