"""Backend-aware model selection: each harness gets its OWN vocabulary.

The dashboard used to answer "which models can I pick?" with kiro-cli's catalog
whatever was actually running, and hid the picker for every other harness to stop
that being visibly wrong. These tests pin the replacement: the vocabulary, the
switch semantics, and the effort control are all resolved from the backend that
is really in play — the LIVE one for a bound slot, the configured one only for a
slot that has not started.

The failure this suite exists to prevent is a picker offering ids the wire
rejects. Every "never receives another backend's list" assertion below is one
concrete way that used to be reachable.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from kiro_crew.acp.model_catalog import (
    CATALOG_ADVERTISED,
    CATALOG_KIRO_CLI,
    CATALOG_NONE,
    CATALOG_REGISTRY,
    REGISTRY_PROVIDER_CLAUDE,
    REGISTRY_PROVIDER_KIRO,
    SCOPE_LIVE_SESSION,
    SCOPE_NEXT_SESSION,
    SCOPE_NONE,
    backend_model_capabilities,
    has_static_catalog,
    model_allowed,
)
from kiro_crew.acp.types import (
    ACP_BACKEND_CLAUDE,
    ACP_BACKEND_CODEX,
    ACP_BACKEND_KAS,
    ACP_BACKEND_KIRO,
    ACP_BACKENDS_REGISTRY_MODEL_CATALOG,
    MODEL_CONFIG_ID,
)
from kiro_crew.config.loader import AgentConfig, coerce_backend_models
from kiro_crew.dashboard.handlers import agents

# A kiro-shaped advertised list. If this ever leaks into a claude or codex
# answer, the picker is offering ids that harness has never heard of.
KIRO_ADVERTISED = [
    {"modelId": "auto", "name": "Auto"},
    {"modelId": "claude-opus-4.8", "name": "Opus 4.8"},
    {"modelId": "claude-sonnet-4.6", "name": "Sonnet 4.6"},
]
CLAUDE_ADVERTISED = [
    {"modelId": "global.anthropic.claude-sonnet-4-6[1m]", "name": "Sonnet 4.6"},
]
CODEX_ADVERTISED = [
    {"modelId": "gpt-5.6-codex", "name": "GPT-5.6 Codex", "description": "Codex default"},
]


class _Provider:
    """A live session's provider, declaring its own harness."""

    def __init__(self, backend: str, models: list[dict] | None = None, switch: bool = True):
        self.acp_backend = backend
        self._models = models or []
        self._switch = switch

    def available_models(self) -> list[dict]:
        return self._models

    def supports_model_switch(self) -> bool:
        return self._switch


def _request(
    *providers: _Provider,
    query: dict[str, str] | None = None,
    slot_provider: _Provider | None = None,
    configured_backend: str = ACP_BACKEND_KIRO,
    configured_models: dict[str, str] | None = None,
) -> MagicMock:
    """A request whose live sessions, ?query and configured backend are explicit."""
    sessions = MagicMock()
    sessions.active_providers = MagicMock(return_value=list(providers))
    sessions.get_provider = MagicMock(return_value=slot_provider)
    state = SimpleNamespace(sessions=sessions)
    request = MagicMock()
    request.app = {"state": state}
    request.query = dict(query or {})
    request._configured = SimpleNamespace(
        agent=AgentConfig(
            acp_backend=configured_backend,
            backend_models=dict(configured_models or {}),
        )
    )
    return request


def _with_config(monkeypatch, request: MagicMock) -> None:
    """Point ``KiroCrewConfig.load()`` at the request's own configured agent."""
    monkeypatch.setattr(agents.KiroCrewConfig, "load", lambda: request._configured)


def _names(resp) -> list[str]:
    return [row["model_name"] for row in json.loads(resp.body)]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── Capability resolution ────────────────────────────────────────────────────


class TestCapabilityResolution:
    def test_kiro_reads_its_own_catalog_and_switches_live(self):
        caps = backend_model_capabilities(ACP_BACKEND_KIRO)
        assert caps.catalog == CATALOG_KIRO_CLI
        assert caps.registry_provider == REGISTRY_PROVIDER_KIRO
        assert caps.selectable and caps.runtime_switch
        assert caps.switch_scope == SCOPE_LIVE_SESSION
        assert caps.reasoning_effort

    def test_kas_shares_kiros_namespace(self):
        # KAS IS kiro-cli (`kiro-cli acp --agent-engine v3`), so reading a
        # different catalog for it would offer ids its own relay rejects.
        assert backend_model_capabilities(ACP_BACKEND_KAS).catalog == CATALOG_KIRO_CLI
        assert backend_model_capabilities(ACP_BACKEND_KAS).registry_provider == (
            REGISTRY_PROVIDER_KIRO
        )

    def test_claude_reads_the_registry_column_not_kiros_catalog(self):
        caps = backend_model_capabilities(ACP_BACKEND_CLAUDE)
        assert caps.catalog == CATALOG_REGISTRY
        assert caps.registry_provider == REGISTRY_PROVIDER_CLAUDE

    def test_codex_has_no_static_catalog_and_says_so(self):
        # Codex has a registry column reserved for it but nothing written there
        # yet, and no `--list-models` to ask. With nothing advertised either, the
        # honest answer is "not selectable", NOT an empty dropdown and NOT a
        # borrowed list. Membership names where a list WOULD come from; whether
        # one exists is a data question, so the column being empty is what keeps
        # this backend out of CATALOG_REGISTRY.
        caps = backend_model_capabilities(ACP_BACKEND_CODEX)
        assert caps.catalog == CATALOG_NONE
        assert not caps.selectable
        assert caps.switch_scope == SCOPE_NONE

    def test_codex_becomes_selectable_once_a_session_advertises(self):
        caps = backend_model_capabilities(ACP_BACKEND_CODEX, advertised=["gpt-5.6-codex"])
        assert caps.catalog == CATALOG_ADVERTISED
        assert caps.selectable

    def test_live_reading_can_only_downgrade_a_switch_claim(self):
        # An adapter build that does not expose the `model` config option is
        # reported as un-switchable rather than optimistically switchable.
        caps = backend_model_capabilities(
            ACP_BACKEND_CODEX, advertised=["gpt-5.6-codex"], live_switch_confirmed=False
        )
        assert not caps.runtime_switch
        assert caps.switch_scope == SCOPE_NEXT_SESSION

    def test_live_reading_cannot_grant_a_switch_kiro_never_claimed(self):
        # A harness outside the membership sets stays un-switchable however
        # enthusiastically a session answers — capability is opt-in, never
        # inherited from an advertisement (harness-parity H6).
        caps = backend_model_capabilities("not-a-backend", live_switch_confirmed=True)
        assert not caps.runtime_switch

    def test_effort_is_a_harness_property_not_a_model_one(self):
        # Codex has demonstrated no effort control, so it gets none even though
        # models in other harnesses with similar names support one.
        assert backend_model_capabilities(ACP_BACKEND_KIRO).reasoning_effort
        assert backend_model_capabilities(ACP_BACKEND_CLAUDE).reasoning_effort
        assert not backend_model_capabilities(ACP_BACKEND_CODEX).reasoning_effort


# ── Backend scoping of the live advertised list ──────────────────────────────


class TestAdvertisedScoping:
    def test_only_same_backend_sessions_are_read(self):
        request = _request(
            _Provider(ACP_BACKEND_KIRO, KIRO_ADVERTISED),
            _Provider(ACP_BACKEND_CODEX, CODEX_ADVERTISED),
        )
        kiro = agents._advertised_for_backend(request, ACP_BACKEND_KIRO)
        codex = agents._advertised_for_backend(request, ACP_BACKEND_CODEX)
        assert [m["modelId"] for m in kiro] == [m["modelId"] for m in KIRO_ADVERTISED]
        assert [m["modelId"] for m in codex] == ["gpt-5.6-codex"]

    def test_claude_never_receives_kiros_advertised_list(self):
        # The concrete leak: a Kiro session is live, a Claude session is not.
        # Reading any live provider would hand Claude kiro's ids.
        request = _request(_Provider(ACP_BACKEND_KIRO, KIRO_ADVERTISED))
        assert agents._advertised_for_backend(request, ACP_BACKEND_CLAUDE) == []

    def test_codex_never_receives_kiros_advertised_list(self):
        request = _request(_Provider(ACP_BACKEND_KIRO, KIRO_ADVERTISED))
        assert agents._advertised_for_backend(request, ACP_BACKEND_CODEX) == []

    def test_provider_that_declares_no_backend_is_skipped_not_counted_as_kiro(self):
        # `None` is the ABC default and means "does not drive an ACP backend".
        # Matching it against "" would make silence a claim to be kiro.
        silent = SimpleNamespace(acp_backend=None, available_models=lambda: KIRO_ADVERTISED)
        request = _request(silent)  # type: ignore[arg-type]
        assert agents._advertised_for_backend(request, ACP_BACKEND_KIRO) == []


# ── Which backend a request is about ─────────────────────────────────────────


class TestBackendResolution:
    def test_explicit_backend_wins(self, monkeypatch):
        request = _request(
            query={"backend": ACP_BACKEND_CODEX}, configured_backend=ACP_BACKEND_KIRO
        )
        _with_config(monkeypatch, request)
        assert agents._resolve_model_backend(request) == ACP_BACKEND_CODEX

    def test_explicit_empty_backend_is_kiro_not_absent(self, monkeypatch):
        # `?backend=` with an empty value IS the kiro backend. Testing
        # truthiness rather than presence would silently fall through to the
        # configured default here.
        request = _request(query={"backend": ""}, configured_backend=ACP_BACKEND_CODEX)
        _with_config(monkeypatch, request)
        assert agents._resolve_model_backend(request) == ACP_BACKEND_KIRO

    def test_unknown_explicit_backend_is_refused_not_substituted(self, monkeypatch):
        request = _request(query={"backend": "nonsense"}, configured_backend=ACP_BACKEND_KIRO)
        _with_config(monkeypatch, request)
        # None -> the handler 400s. Answering with the configured backend's list
        # is exactly the substitution this module exists to prevent.
        assert agents._resolve_model_backend(request) is None

    def test_live_slot_outranks_the_configured_default(self, monkeypatch):
        # The core live-binding contract: an existing Kiro session keeps showing
        # Kiro models after the operator switches the default to Codex.
        request = _request(
            query={"slot": "chat-1"},
            slot_provider=_Provider(ACP_BACKEND_KIRO, KIRO_ADVERTISED),
            configured_backend=ACP_BACKEND_CODEX,
        )
        _with_config(monkeypatch, request)
        assert agents._resolve_model_backend(request) == ACP_BACKEND_KIRO

    def test_live_codex_slot_outranks_a_kiro_default(self, monkeypatch):
        # The mirror direction, so neither backend is privileged by accident.
        request = _request(
            query={"slot": "chat-1"},
            slot_provider=_Provider(ACP_BACKEND_CODEX, CODEX_ADVERTISED),
            configured_backend=ACP_BACKEND_KIRO,
        )
        _with_config(monkeypatch, request)
        assert agents._resolve_model_backend(request) == ACP_BACKEND_CODEX

    def test_live_claude_slot_outranks_a_kiro_default(self, monkeypatch):
        request = _request(
            query={"slot": "chat-1"},
            slot_provider=_Provider(ACP_BACKEND_CLAUDE, CLAUDE_ADVERTISED),
            configured_backend=ACP_BACKEND_KIRO,
        )
        _with_config(monkeypatch, request)
        assert agents._resolve_model_backend(request) == ACP_BACKEND_CLAUDE

    def test_unbound_slot_falls_through_to_the_configured_backend(self, monkeypatch):
        # After a teardown the slot resolves to no provider, so the NEXT session's
        # harness — the configured one — is what the picker must describe.
        request = _request(
            query={"slot": "chat-1"}, slot_provider=None, configured_backend=ACP_BACKEND_CODEX
        )
        _with_config(monkeypatch, request)
        assert agents._resolve_model_backend(request) == ACP_BACKEND_CODEX

    def test_switching_the_configured_backend_changes_an_unbound_vocabulary(self, monkeypatch):
        for backend in (ACP_BACKEND_KIRO, ACP_BACKEND_CLAUDE, ACP_BACKEND_CODEX):
            request = _request(configured_backend=backend)
            _with_config(monkeypatch, request)
            assert agents._resolve_model_backend(request) == backend


# ── The endpoints ────────────────────────────────────────────────────────────


class TestModelsEndpoint:
    def test_codex_with_a_live_session_lists_only_its_own_ids(self, monkeypatch):
        request = _request(
            _Provider(ACP_BACKEND_KIRO, KIRO_ADVERTISED),
            _Provider(ACP_BACKEND_CODEX, CODEX_ADVERTISED),
            query={"backend": ACP_BACKEND_CODEX},
        )
        _with_config(monkeypatch, request)
        names = _names(_run(agents.api_models(request)))
        # Exactly what this backend advertised, and nothing else. No synthetic
        # "auto": codex advertised none, and a harness with no id meaning "let
        # the server choose" cannot be sent one — `_wire_model_id` would have
        # nothing to return and the caller would reset the session, on a backend
        # whose capabilities can report `live_session`.
        assert names == ["gpt-5.6-codex"]
        # And not one real id from the kiro session that is live alongside it.
        # "auto" is excluded from the comparison because it is a sentinel a
        # picker may carry, not a model either backend serves.
        kiro_ids = {m["modelId"] for m in KIRO_ADVERTISED} - {"auto"}
        assert not kiro_ids & set(names)

    def test_an_advertised_auto_is_hoisted_rather_than_invented(self, monkeypatch):
        # The other half: when the harness DOES advertise auto, it leads — so the
        # rule is "hoist, never synthesize", not "never show auto".
        request = _request(
            _Provider(
                ACP_BACKEND_CODEX,
                [
                    {"modelId": "gpt-5.6-codex", "name": "Codex"},
                    {"modelId": "auto", "name": "Auto"},
                ],
            ),
            query={"backend": ACP_BACKEND_CODEX},
        )
        _with_config(monkeypatch, request)
        assert _names(_run(agents.api_models(request))) == ["auto", "gpt-5.6-codex"]

    def test_codex_without_a_session_is_empty_not_borrowed(self, monkeypatch):
        request = _request(
            _Provider(ACP_BACKEND_KIRO, KIRO_ADVERTISED), query={"backend": ACP_BACKEND_CODEX}
        )
        _with_config(monkeypatch, request)
        resp = _run(agents.api_models(request))
        # 200 with [], not a 503: nothing is degraded and retrying cannot help.
        assert resp.status == 200
        assert json.loads(resp.body) == []

    def test_claude_lists_registry_ids_and_never_kiros(self, monkeypatch):
        request = _request(
            _Provider(ACP_BACKEND_KIRO, KIRO_ADVERTISED), query={"backend": ACP_BACKEND_CLAUDE}
        )
        _with_config(monkeypatch, request)
        names = _names(_run(agents.api_models(request)))
        assert "opus-4.8-1m" in names  # a claude_code registry key
        assert "claude-opus-4.8" not in names  # the kiro spelling of the same model
        # No "auto" row: claude-agent-acp declares an EMPTY provider id for it,
        # so there is nothing to send. Offering it anyway would make the one row
        # a user reads as "go back to the default" the only row that resets their
        # session — `_wire_model_id` returns "" for it and the caller falls back
        # to a teardown, on a backend whose capabilities report `live_session`.
        assert "auto" not in names

    def test_unknown_backend_is_a_400_with_a_machine_readable_code(self, monkeypatch):
        request = _request(query={"backend": "nonsense"})
        _with_config(monkeypatch, request)
        resp = _run(agents.api_models(request))
        assert resp.status == 400
        assert json.loads(resp.body)["code"] == "model_list_unknown_backend"


class TestCapabilitiesEndpoint:
    def test_reports_the_live_slots_backend_not_the_configured_one(self, monkeypatch):
        request = _request(
            _Provider(ACP_BACKEND_KIRO, KIRO_ADVERTISED),
            query={"slot": "chat-1"},
            slot_provider=_Provider(ACP_BACKEND_KIRO, KIRO_ADVERTISED),
            configured_backend=ACP_BACKEND_CODEX,
        )
        _with_config(monkeypatch, request)
        body = json.loads(_run(agents.api_model_capabilities(request)).body)
        assert body["backend"] == ACP_BACKEND_KIRO
        assert body["catalog"] == CATALOG_KIRO_CLI
        assert body["reasoning_effort"] is True

    def test_a_session_that_cannot_switch_is_not_reported_as_switchable(self, monkeypatch):
        # "Do not fake it": the pick applies to the next session instead, and the
        # scope says so, so the UI can state the lifetime rather than imply one.
        provider = _Provider(ACP_BACKEND_CODEX, CODEX_ADVERTISED, switch=False)
        request = _request(
            provider,
            query={"slot": "chat-1"},
            slot_provider=provider,
            configured_backend=ACP_BACKEND_CODEX,
        )
        _with_config(monkeypatch, request)
        body = json.loads(_run(agents.api_model_capabilities(request)).body)
        assert body["runtime_switch"] is False
        assert body["switch_scope"] == SCOPE_NEXT_SESSION
        assert body["selectable"] is True

    def test_codex_reports_no_effort_control(self, monkeypatch):
        request = _request(query={"backend": ACP_BACKEND_CODEX})
        _with_config(monkeypatch, request)
        body = json.loads(_run(agents.api_model_capabilities(request)).body)
        assert body["reasoning_effort"] is False
        assert body["selectable"] is False


# ── The configuration contract ───────────────────────────────────────────────


class TestBackendModelConfig:
    def test_kiro_family_still_reads_agent_model(self):
        # Backward compatibility: nothing about an existing kiro config changes.
        cfg = AgentConfig(model="claude-opus-4.8")
        assert cfg.model_for_backend(ACP_BACKEND_KIRO) == "claude-opus-4.8"
        assert cfg.model_for_backend(ACP_BACKEND_KAS) == "claude-opus-4.8"

    def test_adapted_harness_never_inherits_the_kiro_model(self):
        # The whole point of a separate namespace: a kiro id must not be handed
        # to a harness that has never heard of it.
        cfg = AgentConfig(model="claude-opus-4.8")
        assert cfg.model_for_backend(ACP_BACKEND_CLAUDE) == ""
        assert cfg.model_for_backend(ACP_BACKEND_CODEX) == ""

    def test_each_backend_keeps_its_own_saved_pick(self):
        cfg = AgentConfig(
            model="claude-opus-4.8",
            backend_models={"claude": "opus-4.8-1m", "codex": "gpt-5.6-codex"},
        )
        assert cfg.model_for_backend(ACP_BACKEND_KIRO) == "claude-opus-4.8"
        assert cfg.model_for_backend(ACP_BACKEND_CLAUDE) == "opus-4.8-1m"
        assert cfg.model_for_backend(ACP_BACKEND_CODEX) == "gpt-5.6-codex"

    def test_a_pick_survives_switching_away_and_back(self):
        # Switching backend writes `agent.acp_backend` only; each namespace's
        # saved model is untouched, so returning restores the earlier choice.
        saved = {"claude": "opus-4.8-1m", "codex": "gpt-5.6-codex"}
        for backend in (ACP_BACKEND_CODEX, ACP_BACKEND_KIRO, ACP_BACKEND_CLAUDE):
            cfg = AgentConfig(
                model="claude-opus-4.8", acp_backend=backend, backend_models=dict(saved)
            )
            assert cfg.backend_models == saved
        cfg = AgentConfig(model="claude-opus-4.8", backend_models=dict(saved))
        assert cfg.model_for_backend(ACP_BACKEND_CLAUDE) == "opus-4.8-1m"

    def test_auto_collapses_to_inherit_rather_than_pinning_a_literal(self):
        # "auto" is a kiro/registry sentinel, not an id an adapted harness serves.
        # Storing it verbatim would send a literal "auto" to a wire that has no
        # such model.
        assert coerce_backend_models({"claude": "auto", "codex": ""}) == {}
        cfg = AgentConfig(backend_models={"claude": "auto"})
        assert cfg.model_for_backend(ACP_BACKEND_CLAUDE) == ""

    def test_unknown_keys_and_shapes_are_dropped(self):
        assert coerce_backend_models({"kiro": "x", "bogus": "y"}) == {}
        assert coerce_backend_models("not a dict") == {}
        assert coerce_backend_models({"claude": 17}) == {}


class TestAllowlistIsTheSource:
    """One set answers both halves: what the picker offers and what the wire takes.

    The failure these pin is a dropdown row the write path then refuses. It was
    reachable because the two sides were derived independently — the picker read
    a registry column while the set-model guard tested ``agent.provider``, which
    is pinned to ``acp`` for every harness and so could not tell the backends
    apart at all.
    """

    def test_claude_accepts_the_registry_keys_its_own_picker_offers(self):
        # The concrete bug: `opus-4.8-1m` is what /api/models lists for claude
        # AND what claude-agent-acp wants on the wire, but a provider-shaped
        # guard classified it as display-only and answered 400 — so the picker
        # this feature added could not actually switch a model.
        assert model_allowed(ACP_BACKEND_CLAUDE, "opus-4.8-1m")
        assert model_allowed(ACP_BACKEND_CLAUDE, "global.anthropic.claude-opus-4-8[1m]")

    def test_kiro_still_refuses_display_only_canonical_keys(self):
        # The other direction, and the reason one shared rule cannot serve both:
        # the same id kiro-cli rejects with -32603 is claude's wire format. A
        # backend-blind guard has to be wrong for one of them.
        assert not model_allowed(ACP_BACKEND_KIRO, "opus-4.8-1m")
        assert model_allowed(ACP_BACKEND_KIRO, "claude-opus-4.8")

    def test_a_kiro_only_model_is_not_admitted_on_claude(self):
        # `opus-4.6-1m` is a distinct real model on kiro and has no claude_code
        # id at all, so it is absent from claude's allowlist — which is what
        # stops a slot rebound between harnesses from carrying its old pin onto a
        # wire that has never heard of it.
        assert not model_allowed(ACP_BACKEND_CLAUDE, "opus-4.6-1m")

    def test_declared_cross_harness_aliases_are_still_honoured(self):
        # Not every kiro-looking spelling is foreign. The registry deliberately
        # lists some as claude_code ALIASES so claude-agent-acp, which has no
        # distinct model of that name, folds them onto its nearest real one. The
        # allowlist includes aliases for exactly that reason: refusing them would
        # reject values `to_provider_id` already knows how to translate.
        assert model_allowed(ACP_BACKEND_CLAUDE, "claude-opus-4.6")

    def test_provider_default_passes_everywhere(self):
        # "" and "auto" are the ABSENCE of a pick, not ids, so no allowlist can
        # be consulted for them and every harness runs its own default.
        for backend in (ACP_BACKEND_KIRO, ACP_BACKEND_KAS, ACP_BACKEND_CLAUDE, ACP_BACKEND_CODEX):
            assert model_allowed(backend, "")
            assert model_allowed(backend, "auto")

    def test_a_backend_with_no_curated_list_trusts_what_it_advertised(self):
        # Codex's column is empty, so nothing static can vouch for an id. Its own
        # live session naming a model outranks any static heuristic about shape.
        assert model_allowed(ACP_BACKEND_CODEX, "gpt-5.6-codex", advertised=["gpt-5.6-codex"])

    def test_populating_the_column_is_all_it_takes_to_turn_a_picker_on(self):
        # The extension contract: a harness that cannot enumerate its own models
        # gains a real picker by having ids written into its registry column —
        # no code change. Verified by pointing the lookup at a column that IS
        # populated, since codex's is deliberately still empty.
        assert not has_static_catalog(ACP_BACKEND_CODEX)
        assert backend_model_capabilities(ACP_BACKEND_CODEX).catalog == CATALOG_NONE
        assert has_static_catalog(ACP_BACKEND_CLAUDE)
        assert backend_model_capabilities(ACP_BACKEND_CLAUDE).catalog == CATALOG_REGISTRY

    def test_kiro_allowlist_is_not_its_translation_column(self):
        # kiro HAS a registry column — that is how its ids are translated — but
        # reading it as an allowlist would invert this guard: the column's
        # canonical keys would be admitted while a real kiro model the registry
        # has never been taught would be refused.
        assert not has_static_catalog(ACP_BACKEND_KIRO)
        assert model_allowed(ACP_BACKEND_KIRO, "some-model-the-registry-never-heard-of")


class TestConfigOptionReportedVsEmpty:
    """ "Not reported yet" and "reported empty" are opposite answers.

    Collapsing them onto a falsy list is what made the live DOWNGRADE path
    unreachable for the builds it exists for: an adapter that answers
    ``configOptions: []`` has said it offers none, and reading that as "has not
    spoken" reports it as live-switchable. The click then gets ``Unknown config
    option`` and falls back to resetting the user's chat — on a backend whose
    capabilities advertised an in-place switch.
    """

    @staticmethod
    def _client():
        from kiro_crew.acp.client import AcpClient

        return AcpClient.__new__(AcpClient)

    def test_unreported_is_permissive(self):
        client = self._client()
        # None, not []: the backend has not answered. A harness that advertises
        # options lazily must not be permanently branded unsupported.
        client._acp_config_options = None
        assert client.supports_config_option(MODEL_CONFIG_ID)

    def test_reported_empty_is_a_no(self):
        client = self._client()
        client._acp_config_options = []
        assert not client.supports_config_option(MODEL_CONFIG_ID)

    def test_reported_without_this_option_is_a_no(self):
        client = self._client()
        client._acp_config_options = [{"id": "effort"}]
        assert not client.supports_config_option(MODEL_CONFIG_ID)
        assert client.supports_config_option("effort")

    def test_the_two_states_are_one_field_so_they_cannot_drift(self):
        # Encoding "answered?" in the type rather than in a companion boolean is
        # what makes this safe: every writer assigns the list, so none can record
        # the options while forgetting to record that the question was asked.
        client = self._client()
        client._acp_config_options = None
        assert client.supports_config_option(MODEL_CONFIG_ID)
        client._acp_config_options = []
        assert not client.supports_config_option(MODEL_CONFIG_ID)

    def test_the_downgrade_path_is_reachable_for_an_option_less_build(self):
        # The end-to-end point of the distinction: an adapter reporting no
        # options downgrades to a next-session pick instead of promising a live
        # switch it cannot perform.
        caps = backend_model_capabilities(ACP_BACKEND_CLAUDE, live_switch_confirmed=False)
        assert not caps.runtime_switch
        assert caps.switch_scope == SCOPE_NEXT_SESSION


class TestGuardSeesTheSameSetAsThePicker:
    """The read and write paths must share ONE allowlist, advertised half included.

    The picker unions the static catalog with what a live session advertised. A
    guard reading only the static half diverges in BOTH directions, and each
    direction is a real failure: it refuses a model the adapter advertised but
    the registry has not been taught, and it admits an id for a catalog-less
    harness whose session never offered one.
    """

    def test_a_live_advertised_model_the_registry_lacks_is_accepted(self):
        # claude's picker appends adapter-advertised ids the registry does not
        # list, for forward-compat. The guard has to accept what the picker
        # offered, or the newest model is visible and unselectable.
        assert not model_allowed(ACP_BACKEND_CLAUDE, "claude-next-unreleased")
        assert model_allowed(
            ACP_BACKEND_CLAUDE, "claude-next-unreleased", advertised=["claude-next-unreleased"]
        )

    def test_a_catalogless_backend_still_admits_what_it_advertised(self):
        assert model_allowed(ACP_BACKEND_CODEX, "gpt-5.6-codex", advertised=["gpt-5.6-codex"])

    def test_codex_stays_out_of_the_registry_catalog_until_its_wire_is_taught(self):
        # Membership here is not a dormant hook. Adding a `providers.codex` row
        # would immediately make the picker emit canonical registry keys, while
        # `_wire_model_id` translates only the claude namespace and sends
        # everything else through `to_acp_id` — kiro's namespace. A codex session
        # would receive a kiro id.
        assert ACP_BACKEND_CODEX not in ACP_BACKENDS_REGISTRY_MODEL_CATALOG
        assert not has_static_catalog(ACP_BACKEND_CODEX)
