"""Tests for the claude_code model list assembled by /api/models.

The dropdown is scoped to what the account can actually use: the backend's
advertised set is authoritative when present and the static registry is filtered
down to it, so a free-tier account is not offered flagship models it cannot run.
When nothing is advertised (no session yet) the registry is shown unfiltered,
since an empty advertised set cannot be told apart from "entitled to nothing".

Every row is filtered through the backend's ALLOWLIST first -- the same set the
set-model guard admits from -- so nothing offered here can be refused there. That
is what excludes "auto": the registry declares an empty claude_code id for it,
meaning this harness has no way to say "let the server choose", and a pick of it
would silently become a session reset.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from kiro_crew import model_registry
from kiro_crew.acp.model_catalog import REGISTRY_PROVIDER_CLAUDE, registry_provider_for_backend
from kiro_crew.acp.types import ACP_BACKEND_CLAUDE
from kiro_crew.dashboard.handlers.agents import (
    _advertised_registry_rows,
    _registry_backend_models,
)

# Canonical registry rows now lead the dropdown (replaces _CC_CURATED_MODELS).
# The rows the claude picker may offer: every registry entry with a REAL
# claude_code id. `auto` is excluded because its id is empty — see
# `test_registry_set_always_present_even_without_session`.
_REGISTRY_NAMES = [
    r["model_name"] for r in model_registry.display_list("claude_code") if r["model_name"] != "auto"
]


def _advertised(req) -> list[dict]:
    """The claude backend's advertised rows, in the API shape."""
    return _advertised_registry_rows(req, ACP_BACKEND_CLAUDE, REGISTRY_PROVIDER_CLAUDE)


def _models(req, configured_default: str = "") -> list[dict]:
    """The claude backend's assembled dropdown.

    The provider column comes from the capability resolver rather than a literal,
    so this suite fails if the claude backend is ever pointed at a different
    registry column without the test being revisited.
    """
    return _registry_backend_models(
        req,
        ACP_BACKEND_CLAUDE,
        registry_provider_for_backend(ACP_BACKEND_CLAUDE),
        configured_default,
    )


def _request_with_providers(providers: dict) -> MagicMock:
    """Fake aiohttp request whose sessions.active_providers() yields `providers`.

    Mirrors the real SessionManager API (active_providers()) so the test can't
    pass against an attribute the production object doesn't have.
    """
    sessions = SimpleNamespace(active_providers=lambda: list(providers.values()))
    state = SimpleNamespace(sessions=sessions)
    req = MagicMock()
    req.app.__getitem__.return_value = state
    return req


class _FakeProvider:
    """A live provider that declares which harness it is running.

    ``acp_backend`` is not optional scaffolding: the readers filter on it, so a
    provider that did not declare one is skipped rather than counted as kiro
    (harness-parity H5).
    """

    def __init__(self, models, backend: str = ACP_BACKEND_CLAUDE):
        self._models = models
        self.acp_backend = backend

    def available_models(self):
        return self._models


class TestAdvertisedCcModels:
    def test_maps_modelid_name_description(self):
        # An unknown provider id (not in the registry) passes through unchanged.
        prov = _FakeProvider(
            [
                {"modelId": "claude-sonnet-4-6", "name": "Sonnet 4.6", "description": "Everyday"},
            ]
        )
        out = _advertised(_request_with_providers({"s": prov}))
        assert out == [
            {
                "model_name": "claude-sonnet-4-6",
                "display_name": "Sonnet 4.6",
                "description": "Everyday",
            }
        ]

    def test_known_provider_id_mapped_to_canonical_key(self):
        # A backend provider id that IS in the registry maps back to its
        # canonical key so it dedups against the registry rows.
        prov = _FakeProvider(
            [
                {
                    "modelId": "global.anthropic.claude-opus-4-8[1m]",
                    "name": "Opus 4.8",
                    "description": "",
                },
            ]
        )
        out = _advertised(_request_with_providers({"s": prov}))
        assert out[0]["model_name"] == "opus-4.8-1m"

    def test_empty_when_no_active_sessions(self):
        assert _advertised(_request_with_providers({})) == []

    def test_skips_provider_without_accessor(self):
        out = _advertised(_request_with_providers({"s": object()}))
        assert out == []


class TestCcModelsMerge:
    def test_registry_set_always_present_even_without_session(self):
        # No live provider → nothing is advertised, so entitlement is UNKNOWN and
        # the full canonical registry is shown unfiltered. An empty advertised set
        # cannot be distinguished from "this account gets nothing", and an empty
        # picker on a cold dashboard is worse than a superset.
        out = _models(_request_with_providers({}))
        names = [m["model_name"] for m in out]
        assert "opus-4.8-1m" in names
        assert "opus-4.8" in names
        assert set(_REGISTRY_NAMES) <= set(names)
        # No "auto" row. The registry declares an EMPTY claude_code provider id
        # for it, which is the registry's way of saying this harness has no id
        # meaning "let the server choose" — so the row cannot be sent, and a pick
        # of it would fall back to destroying the session while the backend's
        # capabilities advertise an in-place switch. The allowlist excludes it for
        # exactly that reason, and the picker reads the allowlist.
        assert "auto" not in names

    def test_advertised_set_filters_the_registry(self):
        """The advertised set is authoritative: unentitled registry rows go away.

        This is the free-tier case. Previously the registry led unconditionally and
        the adapter could only ADD, so an account served two models was still
        offered the full flagship list and only found out at prompt time.
        """
        prov = _FakeProvider(
            [{"modelId": "global.anthropic.claude-sonnet-4-6[1m]", "name": "Sonnet 4.6"}]
        )
        out = _models(_request_with_providers({"s": prov}))
        names = [m["model_name"] for m in out]
        assert "auto" not in names
        assert "sonnet-4.6-1m" in names
        # The flagship is in the registry but was NOT advertised → filtered out.
        assert "opus-4.8-1m" not in names
        assert "opus-4.8" not in names

    def test_registry_display_name_wins_for_survivors(self):
        """Filtering keeps the registry's cleaner display name, not the adapter's."""
        prov = _FakeProvider(
            [{"modelId": "global.anthropic.claude-sonnet-4-6[1m]", "name": "sonnet-4-6-v1-ugly"}]
        )
        out = _models(_request_with_providers({"s": prov}))
        row = next(m for m in out if m["model_name"] == "sonnet-4.6-1m")
        assert row["display_name"] == "Sonnet 4.6 (1M context)"

    def test_unknown_advertised_models_still_pass_through(self):
        # Forward-compat: a model the registry does not list is still offered when
        # the backend advertises it, otherwise a newly-served model is unreachable.
        prov = _FakeProvider(
            [
                {"modelId": "claude-opus-4-1", "name": "Opus 4.1", "description": ""},
                {"modelId": "claude-sonnet-4-5", "name": "Sonnet 4.5", "description": ""},
            ]
        )
        out = _models(_request_with_providers({"s": prov}))
        names = [m["model_name"] for m in out]
        assert "claude-opus-4-1" in names
        assert "claude-sonnet-4-5" in names
        # And the unentitled registry flagship is gone.
        assert "opus-4.8-1m" not in names
        # "auto" still leads and is never filtered by entitlement -- it is the
        # configured-default sentinel, not a model the backend serves.
        assert "auto" not in names

    def test_configured_default_is_not_resurrected_when_unentitled(self):
        """A stale config pick must not outlive the entitlement.

        Force-including it would reintroduce exactly the unusable option the
        filter removes.
        """
        prov = _FakeProvider(
            [{"modelId": "global.anthropic.claude-sonnet-4-6[1m]", "name": "Sonnet 4.6"}]
        )
        out = _models(_request_with_providers({"s": prov}), configured_default="opus-4.8-1m")
        names = [m["model_name"] for m in out]
        assert "opus-4.8-1m" not in names
        assert "auto" not in names

    def test_configured_default_still_included_when_nothing_advertised(self):
        # Entitlement unknown → trust the operator's config rather than dropping
        # their selected model from the picker.
        out = _models(_request_with_providers({}), configured_default="some-custom-model")
        names = [m["model_name"] for m in out]
        assert "some-custom-model" in names
        assert "auto" not in names

    def test_no_duplicate_when_adapter_lists_known_model(self):
        # The adapter advertises provider ids that ARE in the registry; mapped
        # back to canonical keys they collapse to one row each (registry wins).
        prov = _FakeProvider(
            [
                {
                    "modelId": "global.anthropic.claude-sonnet-4-6[1m]",
                    "name": "Sonnet 4.6",
                    "description": "",
                },
                {
                    "modelId": "global.anthropic.claude-opus-4-8[1m]",
                    "name": "Opus 4.8",
                    "description": "",
                },
            ]
        )
        out = _models(_request_with_providers({"s": prov}))
        names = [m["model_name"] for m in out]
        assert names.count("opus-4.8-1m") == 1
        assert names.count("sonnet-4.6-1m") == 1

    def test_registry_row_keeps_friendly_display_name(self):
        # When the adapter advertises a known id, the registry row (friendly
        # display name) wins over the backend's terser name.
        prov = _FakeProvider(
            [
                {
                    "modelId": "global.anthropic.claude-opus-4-8[1m]",
                    "name": "Opus 4.8",
                    "description": "",
                },
            ]
        )
        out = _models(_request_with_providers({"s": prov}))
        opus48 = next(m for m in out if m["model_name"] == "opus-4.8-1m")
        assert opus48["display_name"] == "Opus 4.8 (1M context)"

    def test_configured_default_force_included(self):
        out = _models(_request_with_providers({}), configured_default="custom-model-xyz")
        names = [m["model_name"] for m in out]
        assert "custom-model-xyz" in names

    def test_configured_default_not_duplicated_if_already_present(self):
        out = _models(
            _request_with_providers({}),
            configured_default="opus-4.8-1m",
        )
        names = [m["model_name"] for m in out]
        assert names.count("opus-4.8-1m") == 1

    def test_configured_default_auto_does_not_insert_blank_row(self):
        # cc_model="auto" round-trips to "" (auto's provider id is empty), which
        # must NOT be inserted as a blank-named row at the top of the dropdown.
        # A blank row would be the FIRST option and would read as the current
        # selection, so a configured value this backend cannot express has to
        # disappear rather than become an unnamed pick.
        out = _models(_request_with_providers({}), configured_default="auto")
        names = [m["model_name"] for m in out]
        assert "" not in names
        assert all(m["model_name"] for m in out)
        # And no "auto" row is synthesized to stand in for it: this backend has
        # no wire id for "let the server choose".
        assert "auto" not in names
