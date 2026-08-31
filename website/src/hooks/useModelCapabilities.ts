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
 * one. Pass `coldStartBackend` as the backend this surface is BOUND to, not the
 * configured default, or a live Codex pane will assume kiro's controls for a
 * frame.
 */
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
    queryKey: ['model-capabilities', slot ?? null, backend ?? null],
    queryFn: () => api.modelCapabilities({ slot, backend }),
    ...(enabled === undefined ? {} : { enabled }),
  })
  return data ?? coldStartCapabilities(coldStartBackend ?? backend)
}
