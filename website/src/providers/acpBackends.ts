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

import { i18nT } from '../i18n/t'

/** The ACP backends this build knows by name. `''` IS kiro-cli, not "unset".
 *
 *  `kas` is here but NOT in `ACP_BACKEND_OPTIONS`: this edition does not offer
 *  it in the dropdown, yet a config written elsewhere can still persist it, and
 *  a backend the type system does not know is one every `switch`/lookup below
 *  silently answers wrong for. */
export type AcpBackend = '' | 'kas' | 'claude' | 'codex'

/** The ids, named. `''` is a legitimate backend, so a call site that spells it
 *  as a bare literal reads as "unset" to the next person and is invisible to a
 *  grep for the harness. Identity is compared against these — positively, never
 *  as `!== ''` or "not the other one", which is the frontend spelling of the
 *  negative-identity rule the server-side capability sets exist to enforce. */
export const ACP_BACKEND_KIRO: AcpBackend = ''
export const ACP_BACKEND_KAS: AcpBackend = 'kas'
export const ACP_BACKEND_CLAUDE: AcpBackend = 'claude'
export const ACP_BACKEND_CODEX: AcpBackend = 'codex'

/** The backends the settings dropdown OFFERS. A persisted value outside this
 *  list is appended by the panel rather than dropped. */
export const ACP_BACKEND_OPTIONS: AcpBackend[] = [
  ACP_BACKEND_KIRO,
  ACP_BACKEND_CLAUDE,
  ACP_BACKEND_CODEX,
]

/** Backends sharing kiro-cli's model namespace and configuration.
 *
 *  Mirrors `ACP_BACKENDS_KIRO_MODEL_CATALOG` server-side. KAS is a member
 *  because it IS kiro-cli behind a relay, so it serves the same model ids and
 *  reads the same `agent.model` — the server's `model_for_backend` says so, and
 *  a frontend that tested `backend === ''` instead would write a KAS user's pick
 *  to a config key the server never reads. */
export const KIRO_MODEL_FAMILY: readonly AcpBackend[] = [ACP_BACKEND_KIRO, ACP_BACKEND_KAS]

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
export const MODEL_CACHE_BACKENDS: AcpBackend[] = [
  ACP_BACKEND_KIRO,
  ACP_BACKEND_KAS,
  ACP_BACKEND_CLAUDE,
  ACP_BACKEND_CODEX,
]

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
  if (backend === ACP_BACKEND_KIRO) return KIRO_COLD_START_CAPABILITIES
  if (backend === ACP_BACKEND_KAS) return KAS_COLD_START_CAPABILITIES
  return UNKNOWN_MODEL_CAPABILITIES
}

/** What to CALL a harness in the UI.
 *
 *  One resolver, shared by Settings ▸ Chat and the composer's backend control,
 *  so the two surfaces cannot name the same harness differently. Kiro's id is
 *  the empty string, so a surface that rendered the id would print a blank
 *  where the harness belongs.
 *
 *  A backend outside the offered set still gets a name rather than nothing: a
 *  config written by hand (or by an edition) can persist one, and the control
 *  has to be able to say what the slot is on before it can offer to move it. */
export function acpBackendLabel(backend: string): string {
  if (backend === ACP_BACKEND_KIRO) return i18nT('pages.settings.chatPanel.backend_kiro_cli')
  if (backend === ACP_BACKEND_CLAUDE) return i18nT('pages.settings.chatPanel.backend_claude_code')
  if (backend === ACP_BACKEND_CODEX) return i18nT('pages.settings.chatPanel.backend_codex')
  return i18nT('pages.settings.chatPanel.backend_external', { backend })
}

/**
 * The harness a slot's next session runs on: the slot's OWN binding, and the
 * configured default only for a slot that has never been bound.
 *
 * The asymmetry is the whole point, and it is the server's
 * (`_slot_backend` / `resolve_session_backend`): `agent.acp_backend` is the
 * default for a slot with no binding of its own, never an override of one that
 * has. So `??`, never `||` — `''` IS the kiro harness, and a truthiness test
 * would read a slot deliberately bound to kiro as unbound and hand it whatever
 * the default happens to say.
 *
 * One helper because the answer feeds a query KEY (the model list and the
 * capability answer are both scoped by it). Three private copies of the same
 * coalesce is how one surface ends up asking about a harness the session is not
 * on, and the failure is silent: it renders another harness's model vocabulary.
 */
export function resolveSlotBackend(
  slotBackend: string | null | undefined,
  configuredBackend: string | null | undefined,
): string {
  return slotBackend ?? configuredBackend ?? ACP_BACKEND_KIRO
}

/** `POST /api/chat/slots/{slot}/backend` — the outcome of moving one slot.
 *
 *  `changed` is whether the harness the next session is created on moved;
 *  `reset` is whether a conversation was discarded and will be replayed onto
 *  the new harness; `model_cleared` reports a dropped model pin, with `model`
 *  carrying what the slot holds afterwards (empty = inherit whatever the
 *  backend serves). The pin is dropped rather than translated — the
 *  vocabularies are disjoint — but never silently, which is what
 *  `model_cleared` is for. */
export interface SlotBackendResult {
  ok?: boolean
  slot?: string
  backend: string
  changed: boolean
  reset: boolean
  model_cleared: boolean
  model: string
}
