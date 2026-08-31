/**
 * ACP backend vocabulary and model-capability shape, shared by every surface
 * that renders a model control.
 *
 * This lived inside Settings ▸ Chat while only that page knew backends existed.
 * The composer, the split pane, the model hook and the localStorage cache all
 * need it now, and four private copies of a string union is how one of them ends
 * up not knowing about a backend the other three do.
 *
 * ## Why capabilities come from the server
 *
 * The frontend must not infer what a harness can do from WHICH harness it is.
 * `backend === ''` reads correctly while kiro is the only selectable one and
 * then silently denies the next harness a control it supports, or offers one it
 * does not — the frontend spelling of the negative-identity problem the backend
 * solves with opt-in membership sets. `GET /api/model-capabilities` answers the
 * question directly, including the parts only a LIVE session can know (whether
 * this adapter build actually exposes the model config option), so there is
 * nothing here to keep in sync with the harness list.
 */

/** The ACP backends this build knows by name. `''` IS kiro-cli, not "unset".
 *
 *  `kas` is here but NOT in `ACP_BACKEND_OPTIONS`: this edition does not offer
 *  it in the dropdown, yet a config written elsewhere can still persist it, and
 *  a backend the type system does not know is one every `switch`/lookup below
 *  silently answers wrong for. */
export type AcpBackend = '' | 'kas' | 'claude' | 'codex'

/** The backends the settings dropdown OFFERS. A persisted value outside this
 *  list is appended by the panel rather than dropped. */
export const ACP_BACKEND_OPTIONS: AcpBackend[] = ['', 'claude', 'codex']

/** Backends sharing kiro-cli's model namespace and configuration.
 *
 *  Mirrors `ACP_BACKENDS_KIRO_MODEL_CATALOG` server-side. KAS is a member
 *  because it IS kiro-cli behind a relay, so it serves the same model ids and
 *  reads the same `agent.model` — the server's `model_for_backend` says so, and
 *  a frontend that tested `backend === ''` instead would write a KAS user's pick
 *  to a config key the server never reads. */
export const KIRO_MODEL_FAMILY: readonly AcpBackend[] = ['', 'kas']

/** Whether `backend` resolves its model through `agent.model` (the kiro family)
 *  rather than its own `agent.backend_models` entry.
 *
 *  Only an EXPLICIT kiro identity counts. `undefined` means "whatever is
 *  configured", which is a different question and is false here: coalescing it
 *  to `''` would let a Claude or Codex dashboard whose model request failed fall
 *  through to kiro's `auto` fallback — offering the one cross-backend option
 *  this module exists to keep out of an adapted harness's picker. */
export function isKiroModelFamily(backend: string | undefined): boolean {
  if (backend === undefined) return false
  return (KIRO_MODEL_FAMILY as readonly string[]).includes(backend)
}

/** Backends whose model list is cached separately in localStorage.
 *
 *  Every backend this build can encounter, not just the offered ones: one
 *  shared cache entry would let the last backend to fetch decide what every
 *  picker serves on the next cold start. */
export const MODEL_CACHE_BACKENDS: AcpBackend[] = ['', 'kas', 'claude', 'codex']

/** Where a backend's picker options come from (`catalog` below). */
export type ModelCatalogSource =
  /** kiro-cli's own `--list-models`, narrowed by entitlement. */
  | 'kiro_cli'
  /** A static registry column for this backend's provider, narrowed by entitlement. */
  | 'registry'
  /** Only what a live session advertised over ACP — no static list exists. */
  | 'advertised'
  /** Nothing to offer: no static catalog and nothing advertised yet. */
  | 'none'

/** What a model pick actually does, so the UI can state the lifetime. */
export type ModelSwitchScope =
  /** Applies to the running session, in place. */
  | 'live_session'
  /** Persists as the default and applies to the NEXT session. */
  | 'next_session'
  /** No user-selectable model on this backend. */
  | 'none'

/** `GET /api/model-capabilities` — what a client may offer for one backend. */
export interface ModelCapabilities {
  backend: string
  catalog: ModelCatalogSource
  registry_provider: string
  /** False means: render the reason, never an empty dropdown. */
  selectable: boolean
  runtime_switch: boolean
  switch_scope: ModelSwitchScope
  /** Whether the HARNESS has an effort control. The selected MODEL must also
   *  support one — a separate question, answered by `modelSupportsEffort`. */
  reasoning_effort: boolean
}

/** Capabilities assumed for an ADAPTED harness before the server answers.
 *
 *  The conservative shape: nothing selectable, no effort control. A permissive
 *  placeholder would flash a picker that then vanishes on a backend that has
 *  none, and — worse — would briefly offer options from whatever the previous
 *  shape implied. */
export const UNKNOWN_MODEL_CAPABILITIES: ModelCapabilities = {
  backend: '',
  catalog: 'none',
  registry_provider: '',
  selectable: false,
  runtime_switch: false,
  switch_scope: 'none',
  reasoning_effort: false,
}

/** Capabilities assumed for the KIRO backend before the server answers.
 *
 *  Every field here is unconditionally true of kiro-cli: it reads its own
 *  `--list-models` catalog, switches a live session with `session/set_model`,
 *  and carries a reasoning-effort control. */
const KIRO_COLD_START_CAPABILITIES: ModelCapabilities = {
  backend: '',
  catalog: 'kiro_cli',
  registry_provider: 'acp',
  selectable: true,
  runtime_switch: true,
  switch_scope: 'live_session',
  reasoning_effort: true,
}

/** Capabilities assumed for KAS before the server answers.
 *
 *  KAS is kiro-cli behind a relay, so its catalog and its effort control are as
 *  certain as kiro's. Its model SWITCH is not: it implements no
 *  `session/set_model` and moves the model through the config-option channel,
 *  whose presence depends on the build. Claiming `live_session` here would
 *  promise an in-place switch that silently becomes a session reset, so the
 *  cold-start answer is the one that cannot lie — the pick applies to the next
 *  session until a live reading says better. */
const KAS_COLD_START_CAPABILITIES: ModelCapabilities = {
  backend: 'kas',
  catalog: 'kiro_cli',
  registry_provider: 'acp',
  selectable: true,
  runtime_switch: false,
  switch_scope: 'next_session',
  reasoning_effort: true,
}

/**
 * What to assume while `/api/model-capabilities` has not answered.
 *
 * This is NOT the capability-by-identity inference the server-side design
 * removes — it is a bounded cold-start default, replaced by the real answer the
 * moment one arrives, and it is deliberately asymmetric:
 *
 * - **kiro gets its real shape.** kiro-cli is the product's floor: it is in
 *   `ACP_BACKENDS_SELECTABLE` unconditionally and its three model capabilities
 *   are not build-dependent, so there is nothing for a live reading to discover.
 *   Assuming conservatively for it means the model chip and the effort control
 *   DISAPPEAR on every cold load, and stay gone if the endpoint is unreachable
 *   (a version-skewed gateway serving an older API to a newer bundle). Losing
 *   the picker outright is a worse failure than a one-render flash.
 * - **KAS gets kiro's catalog but not kiro's switch**, the one place the two
 *   halves of "is this the kiro family?" give different answers.
 * - **every other adapted harness gets the conservative shape**, because for
 *   those the answer genuinely is unknown until the server speaks: the catalog
 *   may be empty and the switch channel may not exist in this adapter build.
 *   Guessing permissively there is exactly what would offer a control the wire
 *   rejects.
 */
export function coldStartCapabilities(backend: string | undefined): ModelCapabilities {
  if (backend === '') return KIRO_COLD_START_CAPABILITIES
  if (backend === 'kas') return KAS_COLD_START_CAPABILITIES
  return UNKNOWN_MODEL_CAPABILITIES
}
