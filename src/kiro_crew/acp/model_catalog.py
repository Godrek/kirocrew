"""Backend-aware model-selection capabilities — one answer per ACP harness.

The dashboard used to ask "is this the kiro backend?" and hide the model picker
when it was not, because kiro's advertised list is the only vocabulary the
``/api/models`` path knew how to produce. That is a truthful UI only while
exactly one harness can select a model; the moment a second one can, the
question the picker actually needs answered is not *which backend is this* but:

1. where do this backend's options come from (:data:`CATALOG_KIRO_CLI` /
   :data:`CATALOG_REGISTRY` / :data:`CATALOG_ADVERTISED` / :data:`CATALOG_NONE`),
2. can a LIVE session change model in place, and
3. does the harness carry a reasoning-effort control at all.

Each is resolved from an opt-in membership set in :mod:`kiro_crew.acp.types`
(harness-parity H6/H7), so a harness added later inherits nothing by omission.
This module is the only place those three sets are read together, so the API,
the settings page, and the composer cannot disagree about what a backend can do.

**Static claim vs live confirmation.** Membership states what a harness can do in
principle; it cannot state what a particular adapter BUILD does. So the
config-option backends carry a static claim that is downgraded — never upgraded —
by ``supports_config_option(MODEL_CONFIG_ID)`` on a live session. A missing live
reading (no session yet) leaves the static claim standing, because the
alternative is telling a user their backend cannot switch models when nothing has
been asked yet.

**Never borrow a vocabulary.** A backend in neither catalog set has no static
list, and the correct picker for it is built from what its own session
advertised — or, with nothing advertised, no picker at all plus an honest
statement that the backend picks its own model. Substituting another harness's
list is the specific failure this module exists to prevent: every id in it would
be one the wire rejects.

**One allowlist, read by both halves.** :func:`allowed_model_ids` is the set of
ids a backend accepts, and it answers for the picker (what to OFFER) and the
set-model guard (what to ADMIT) alike. Deriving those separately is how a picker
comes to list a model whose id the write path then refuses — the read side is
where a wrong answer is visible and the write side is where it is expensive, so
they must not be two rules. A harness that can enumerate its own models supplies
its allowlist at runtime; one that cannot (claude-agent-acp has no list-models
call) is served from a curated column in ``model_registry.json``, which is where
a newly-supported model is added.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from kiro_crew import model_registry
from kiro_crew.acp.types import (
    ACP_BACKEND_CLAUDE,
    ACP_BACKEND_CODEX,
    ACP_BACKENDS_KIRO_MODEL_CATALOG,
    ACP_BACKENDS_MODEL_CONFIG_OPTION,
    ACP_BACKENDS_REASONING_EFFORT,
    ACP_BACKENDS_REGISTRY_MODEL_CATALOG,
    ACP_BACKENDS_RUNTIME_MODEL_SWITCH,
)

#: Values meaning "no explicit model — run the backend's own default". Accepted
#: on every harness because they are the ABSENCE of a pick rather than an id, so
#: no allowlist can be consulted for them.
PROVIDER_DEFAULT_MODELS = frozenset({"", "auto"})

# ── Catalog sources ──
# What a picker's option list is BUILT from. Reported to the frontend so it can
# explain an empty or degraded list instead of rendering a blank dropdown.

#: kiro-cli's own ``chat --list-models`` catalog, narrowed by entitlement.
CATALOG_KIRO_CLI = "kiro_cli"
#: The static ``model_registry`` column for this backend's provider, narrowed by
#: what a live session advertised.
CATALOG_REGISTRY = "registry"
#: Only what a live session advertised over ACP. No static list exists, so the
#: picker is empty until a session of this backend has initialized.
CATALOG_ADVERTISED = "advertised"
#: Nothing to offer: no static catalog and nothing advertised yet.
CATALOG_NONE = "none"

# ── Registry provider columns ──
# Which ``model_registry.json`` provider column names this backend's ids. ``""``
# means the registry does not know this backend and ids pass through unchanged —
# the registry's documented identity-preserving contract.
REGISTRY_PROVIDER_KIRO = "acp"
REGISTRY_PROVIDER_CLAUDE = "claude_code"
REGISTRY_PROVIDER_CODEX = "codex"
REGISTRY_PROVIDER_NONE = ""

#: Which ``model_registry.json`` provider column holds each backend's allowlist.
#: The registry file is the one place model ids may be written down (the
#: hardcoded-model gate exempts ``model_registry*`` and nothing else), so adding
#: support for a model on a harness that cannot enumerate its own — the whole
#: reason an allowlist exists — is a data edit there and nothing else. A backend
#: absent from this map has no column and no static vocabulary.
_BACKEND_REGISTRY_COLUMN: dict[str, str] = {
    **{b: REGISTRY_PROVIDER_KIRO for b in ACP_BACKENDS_KIRO_MODEL_CATALOG},
    ACP_BACKEND_CLAUDE: REGISTRY_PROVIDER_CLAUDE,
    ACP_BACKEND_CODEX: REGISTRY_PROVIDER_CODEX,
}

# ── Model-change lifetime ──
# What a user's pick actually does, so the UI can say so rather than implying
# every pick takes effect immediately.

#: Applies to the running session, in place.
SCOPE_LIVE_SESSION = "live_session"
#: Persists as the default and applies to the NEXT session; the running process
#: is deliberately left alone (a default change must not kill an in-flight turn).
SCOPE_NEXT_SESSION = "next_session"
#: No user-selectable model on this backend.
SCOPE_NONE = "none"


@dataclass(frozen=True)
class BackendModelCapabilities:
    """What one ACP backend can do about models, resolved for one call site."""

    #: The ACP backend id these capabilities describe (``""`` = kiro-cli).
    backend: str
    #: One of the ``CATALOG_*`` constants.
    catalog: str
    #: One of the ``REGISTRY_PROVIDER_*`` constants.
    registry_provider: str
    #: Whether a user may choose a model at all. False means: render the reason,
    #: not an empty dropdown.
    selectable: bool
    #: Whether a LIVE session accepts a model change in place.
    runtime_switch: bool
    #: One of the ``SCOPE_*`` constants — the lifetime of a pick.
    switch_scope: str
    #: Whether the harness carries a reasoning-effort control. The SELECTED
    #: model must also support effort; that is a separate, model-level question
    #: answered by ``effort.model_supports_effort``.
    reasoning_effort: bool

    def to_api(self) -> dict[str, object]:
        """JSON body for ``GET /api/model-capabilities``.

        Snake-case keys, matching every other dashboard payload.
        """
        return {
            "backend": self.backend,
            "catalog": self.catalog,
            "registry_provider": self.registry_provider,
            "selectable": self.selectable,
            "runtime_switch": self.runtime_switch,
            "switch_scope": self.switch_scope,
            "reasoning_effort": self.reasoning_effort,
        }


def registry_provider_for_backend(backend: str) -> str:
    """The ``model_registry`` provider column naming *backend*'s model ids.

    ``REGISTRY_PROVIDER_NONE`` for a backend the registry does not cover, where
    ids must pass through untranslated rather than being folded onto another
    harness's spelling of a similarly-named model.
    """
    return _BACKEND_REGISTRY_COLUMN.get(backend, REGISTRY_PROVIDER_NONE)


def allowed_model_ids(backend: str, *, advertised: Sequence[str] | None = None) -> frozenset[str]:
    """Every model id *backend* will accept — its allowlist.

    The single source both halves of the feature read: the picker offers from it
    and the set-model guard admits from it. Two independent answers is how a
    dropdown comes to list an id the wire then rejects with -32603, which is the
    specific failure this returns one set to prevent.

    Union of the backend's registry column (the curated part, edited in
    ``model_registry.json``) and what a live session advertised (the discovered
    part). Union rather than intersection because each covers the other's gap: a
    harness may serve a model the registry has not been taught yet, and the
    registry is the only vocabulary available before any session exists.

    Only a backend in :data:`ACP_BACKENDS_REGISTRY_MODEL_CATALOG` draws the
    curated half. The kiro family has a registry COLUMN — that is how its ids get
    translated — but its allowlist is the catalog kiro-cli enumerates at runtime,
    which is a superset of what the registry happens to name. Reading its column
    as an allowlist would quietly do the opposite of what this function is for:
    the column's canonical KEYS (``opus-4.8-1m``) are display-only identifiers
    kiro-cli rejects, so they would be admitted while a real kiro model the
    registry has not been taught would not.

    Empty means "no allowlist for this backend" — nothing is known, NOT nothing
    is permitted. Callers distinguish the two; see :func:`model_allowed`.
    """
    ids: set[str] = set()
    if backend in ACP_BACKENDS_REGISTRY_MODEL_CATALOG:
        ids |= model_registry.wire_allowlist(registry_provider_for_backend(backend))
    ids.update(m for m in (advertised or ()) if m)
    return frozenset(ids)


def has_static_catalog(backend: str) -> bool:
    """Whether *backend*'s registry column is actually populated.

    Membership in :data:`ACP_BACKENDS_REGISTRY_MODEL_CATALOG` says the column is
    where this backend's list WOULD come from; this says whether one has been
    written yet. Keeping them separate is what lets a harness be wired up before
    its models are known: it serves what its sessions advertise today, and starts
    serving the curated list the moment a row is added — with no code change.
    """
    if backend not in ACP_BACKENDS_REGISTRY_MODEL_CATALOG:
        return False
    return bool(model_registry.wire_allowlist(registry_provider_for_backend(backend)))


def model_allowed(backend: str, model_id: str, *, advertised: Sequence[str] | None = None) -> bool:
    """Whether *backend* accepts *model_id* as a wire model.

    ``""``/``auto`` always pass: they are the absence of a pick, and every
    harness runs its own default when told nothing.

    A backend with a CURATED allowlist is answered by membership in it.

    A backend without one cannot be answered positively: the kiro family
    enumerates its catalog at runtime, so no synchronous set exists here, and
    refusing on an unknown set would reject every model it serves. There the
    check narrows to the one error a static rule can still catch — a DISPLAY-ONLY
    canonical key (``opus-4.8-1m``) reaching a wire that speaks bare dotted ids,
    which kiro-cli rejects with -32603 and which would persist into
    ``slot.model`` and break the next turn. Anything the backend itself
    advertised passes ahead of that rule, because a live session naming its own
    model outranks a static heuristic about the shape of an id.
    """
    if model_id in PROVIDER_DEFAULT_MODELS:
        return True
    if not has_static_catalog(backend):
        if model_id in {m for m in (advertised or ()) if m}:
            return True
        return not model_registry.is_canonical_key(model_id)
    return model_id in allowed_model_ids(backend, advertised=advertised)


def _resolve_catalog(backend: str, advertised: Sequence[str] | None) -> str:
    """Which source builds *backend*'s option list, given what it advertised."""
    if backend in ACP_BACKENDS_KIRO_MODEL_CATALOG:
        return CATALOG_KIRO_CLI
    if has_static_catalog(backend):
        return CATALOG_REGISTRY
    # No static vocabulary. The live session's own advertisement is the only
    # truthful source, and with nothing advertised there is genuinely nothing to
    # offer — which the UI states rather than rendering an empty control.
    return CATALOG_ADVERTISED if advertised else CATALOG_NONE


def _resolve_runtime_switch(backend: str, live_switch_confirmed: bool | None) -> bool:
    """Whether a live session on *backend* takes a model change in place.

    *live_switch_confirmed* is the session's own
    ``supports_config_option(MODEL_CONFIG_ID)`` reading, or ``None`` when no
    session exists to ask. It can only DOWNGRADE the static claim: a harness
    outside :data:`ACP_BACKENDS_RUNTIME_MODEL_SWITCH` never becomes switchable
    because one adapter happened to advertise a ``model`` option, and a harness
    inside it is not declared unswitchable merely because nothing has started.
    """
    if backend not in ACP_BACKENDS_RUNTIME_MODEL_SWITCH:
        return False
    # Only the config-option channel is build-dependent. ``session/set_model`` is
    # a protocol method rather than an advertised option, so there is nothing for
    # a live reading to confirm and consulting one would let an unrelated absence
    # withdraw a capability kiro-cli always has.
    if backend in ACP_BACKENDS_MODEL_CONFIG_OPTION and live_switch_confirmed is False:
        return False
    return True


def backend_model_capabilities(
    backend: str,
    *,
    advertised: Sequence[str] | None = None,
    live_switch_confirmed: bool | None = None,
) -> BackendModelCapabilities:
    """Resolve *backend*'s model capabilities for one call site.

    *advertised* is the model-id list a live session of this backend reported at
    ``session/new`` (empty/``None`` when none has). It only matters for a backend
    with no static catalog, where it is the difference between a real picker and
    an honest "this backend picks its own model".

    *live_switch_confirmed* is that session's ``supports_config_option`` reading;
    see :func:`_resolve_runtime_switch`.
    """
    catalog = _resolve_catalog(backend, advertised)
    selectable = catalog != CATALOG_NONE
    runtime_switch = _resolve_runtime_switch(backend, live_switch_confirmed)
    if not selectable:
        switch_scope = SCOPE_NONE
    elif runtime_switch:
        switch_scope = SCOPE_LIVE_SESSION
    else:
        # Selectable but not switchable in place. The pick is persisted as the
        # default and the running process is left alone deliberately — a default
        # change must never tear down an in-flight turn.
        switch_scope = SCOPE_NEXT_SESSION
    return BackendModelCapabilities(
        backend=backend,
        catalog=catalog,
        registry_provider=registry_provider_for_backend(backend),
        selectable=selectable,
        runtime_switch=runtime_switch,
        switch_scope=switch_scope,
        reasoning_effort=backend in ACP_BACKENDS_REASONING_EFFORT,
    )
