"""Choosing the ACP harness when a chat slot is created.

``POST /api/chat/slots`` ends with ``schedule_eager_spawn``: with
``session.eager_spawn`` on, the session is being created while the user is still
deciding, so a harness picked a moment later is not the cheap unbound case the
per-slot switch handles — it has a live session to tear down on a conversation
that never had a turn. ``backend`` in the create body records the binding BEFORE
the spawn is scheduled, so the speculative session is born on the harness the
caller asked for. The fork route accepts the same key for the same reason.

What decides whether this is correct:

**The pick is in place when the spawn is scheduled.** Not merely on the slot
afterwards — the eager task snapshots the binding when it runs.

**An explicit pick is refused, never substituted.** Unknown or non-selectable
values are a 400 with a code and mint nothing. Absent means the configured
default, which is what every caller that does not know about harnesses relies on.

**The pick survives an unclaimed teardown.** ``_on_provider_unbound`` unbinds an
empty slot on the presumption that its binding was speculative; a pick made at
creation is the case where that presumption is wrong, and losing it would hand
the first turn to the default the user deliberately did not choose.
"""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from kiro_crew.acp.types import (
    ACP_BACKEND_CLAUDE,
    ACP_BACKEND_CODEX,
    ACP_BACKEND_KAS,
    ACP_BACKEND_KIRO,
    ACP_BACKENDS_DASHBOARD_SELECTABLE,
)
from kiro_crew.dashboard.chat_fork import api_chat_slot_fork
from kiro_crew.dashboard.chat_handlers import api_chat_slot_create

# Allowed on the claude harness only, so it is the pin another harness drops.
CLAUDE_ONLY_MODEL = "opus-4.8-1m"


def _make_app(state) -> web.Application:
    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/chat/slots", api_chat_slot_create)
    app.router.add_post("/api/chat/slots/{slot}/fork", api_chat_slot_fork)
    return app


@contextlib.contextmanager
def _configured(backend: str):
    """Pin ``agent.acp_backend`` and neutralise the rest of the config read."""
    cfg = MagicMock()
    cfg.agent.acp_backend = backend
    cfg.dashboard.default_project = ""
    with patch("kiro_crew.dashboard.chat_handlers.KiroCrewConfig.load", return_value=cfg):
        yield


@contextlib.contextmanager
def _eager_spawn_probe():
    """Replace the eager spawn with a probe that records the binding it SAW.

    The eager task reads ``slot.acp_backend`` when it runs, so what matters is
    the value at the moment the spawn is scheduled, not the value the response
    is built from afterwards.
    """
    seen: list[str | None] = []

    def _record(state, slot, **_kwargs):
        seen.append(slot.acp_backend)
        return None

    with patch("kiro_crew.dashboard.chat_handlers.schedule_eager_spawn", side_effect=_record):
        yield seen


async def _create(app: web.Application, payload: dict):
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/chat/slots", json=payload)
        return resp.status, await resp.json()


async def _fork(app: web.Application, slot: str, payload: dict):
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(f"/api/chat/slots/{slot}/fork", json=payload)
        return resp.status, await resp.json()


class TestCreatingOnAHarness:
    @pytest.mark.asyncio
    async def test_the_binding_is_recorded_before_the_eager_spawn(self, tmp_path):
        state = _make_state(tmp_path)

        with _configured(ACP_BACKEND_KIRO), _eager_spawn_probe() as seen:
            status, body = await _create(_make_app(state), {"backend": ACP_BACKEND_CODEX})

        assert status == 200
        assert body["acp_backend"] == ACP_BACKEND_CODEX
        # The spawn ran exactly once and saw the pick, not the default.
        assert seen == [ACP_BACKEND_CODEX]
        slot = state._slots[body["key"]]
        assert slot.acp_backend == ACP_BACKEND_CODEX
        assert slot._backend_explicit is True
        assert slot._dirty is True

    @pytest.mark.asyncio
    async def test_kiro_is_sent_as_the_empty_string_and_read_by_presence(self, tmp_path):
        """``""`` IS the kiro harness: with codex configured, an explicit kiro pick
        must not read as "no pick" and land the slot on codex."""
        state = _make_state(tmp_path)

        with _configured(ACP_BACKEND_CODEX), _eager_spawn_probe() as seen:
            status, body = await _create(_make_app(state), {"backend": ACP_BACKEND_KIRO})

        assert status == 200
        assert body["acp_backend"] == ACP_BACKEND_KIRO
        assert seen == [ACP_BACKEND_KIRO]
        assert state._slots[body["key"]].acp_backend == ACP_BACKEND_KIRO

    @pytest.mark.asyncio
    async def test_an_absent_backend_leaves_the_slot_unbound(self, tmp_path):
        """Apps, MCP, the CLI and sub-agents create slots without the key, and for
        them absent must keep meaning "the configured default" — an unbound slot
        whose eager spawn picks the default of that moment."""
        state = _make_state(tmp_path)

        with _configured(ACP_BACKEND_CODEX), _eager_spawn_probe() as seen:
            status, body = await _create(_make_app(state), {})

        assert status == 200
        assert body["acp_backend"] is None
        assert seen == [None]
        slot = state._slots[body["key"]]
        assert slot.acp_backend is None
        assert slot._backend_explicit is False

    @pytest.mark.asyncio
    async def test_a_model_is_judged_against_the_picked_harness(self, tmp_path):
        """A valid pairing survives the way in even though the CONFIGURED
        harness could not run it; the pick is the harness that matters."""
        state = _make_state(tmp_path)

        with _configured(ACP_BACKEND_KIRO), _eager_spawn_probe():
            status, body = await _create(
                _make_app(state), {"backend": ACP_BACKEND_CLAUDE, "model": CLAUDE_ONLY_MODEL}
            )

        assert status == 200
        assert body["model"] == CLAUDE_ONLY_MODEL
        assert state._slots[body["key"]].model == CLAUDE_ONLY_MODEL

    @pytest.mark.asyncio
    async def test_a_model_the_picked_harness_cannot_run_is_dropped_to_inherit(self, tmp_path):
        """Dropped, never translated: the slot inherits what the harness serves
        rather than being handed an override that dies on the first prompt."""
        state = _make_state(tmp_path)

        with _configured(ACP_BACKEND_CLAUDE), _eager_spawn_probe():
            status, body = await _create(
                _make_app(state), {"backend": ACP_BACKEND_CODEX, "model": CLAUDE_ONLY_MODEL}
            )

        assert status == 200
        assert body["model"] == ""
        assert state._slots[body["key"]].model == ""


class TestRefusingAPick:
    @pytest.mark.asyncio
    async def test_a_selectable_but_not_dashboard_offered_harness_is_refused(self, tmp_path):
        """The create body is not a wider door than the settings page: KAS is
        selectable by an edition, but not one of the dashboard's public choices."""
        assert ACP_BACKEND_KAS not in ACP_BACKENDS_DASHBOARD_SELECTABLE
        state = _make_state(tmp_path)

        with _configured(ACP_BACKEND_KIRO), _eager_spawn_probe() as seen:
            status, body = await _create(_make_app(state), {"backend": ACP_BACKEND_KAS})

        assert status == 400
        assert body["code"] == "backend_not_selectable"
        # Refused before anything is minted: no slot, no spawn.
        assert state._slots == {}
        assert seen == []

    @pytest.mark.asyncio
    async def test_a_non_string_backend_is_refused(self, tmp_path):
        state = _make_state(tmp_path)

        with _configured(ACP_BACKEND_KIRO), _eager_spawn_probe() as seen:
            status, body = await _create(_make_app(state), {"backend": 7})

        assert status == 400
        assert body["code"] == "backend_invalid"
        assert state._slots == {}
        assert seen == []

    @pytest.mark.asyncio
    async def test_an_unknown_backend_is_never_resolved_onto_the_default(self, tmp_path):
        state = _make_state(tmp_path)

        with _configured(ACP_BACKEND_KIRO), _eager_spawn_probe():
            status, body = await _create(_make_app(state), {"backend": "no-such-harness"})

        assert status == 400
        assert body["code"] == "backend_not_selectable"
        assert state._slots == {}


class TestNamingAnExistingSlot:
    """``name`` can address a slot that already exists, and its harness is its own."""

    @pytest.mark.asyncio
    async def test_a_differing_pick_is_refused_and_pointed_at_the_switch_route(self, tmp_path):
        state = _make_state(tmp_path)
        existing = state.get_or_create_slot("chat-1")
        existing.acp_backend = ACP_BACKEND_CLAUDE

        with _configured(ACP_BACKEND_KIRO), _eager_spawn_probe() as seen:
            status, body = await _create(
                _make_app(state), {"name": "chat-1", "backend": ACP_BACKEND_CODEX}
            )

        assert status == 409
        assert body["code"] == "slot_backend_bound"
        assert existing.acp_backend == ACP_BACKEND_CLAUDE
        assert seen == []

    @pytest.mark.asyncio
    async def test_the_same_harness_is_a_no_op(self, tmp_path):
        state = _make_state(tmp_path)
        existing = state.get_or_create_slot("chat-1")
        existing.acp_backend = ACP_BACKEND_CODEX

        with _configured(ACP_BACKEND_KIRO), _eager_spawn_probe():
            status, body = await _create(
                _make_app(state), {"name": "chat-1", "backend": ACP_BACKEND_CODEX}
            )

        assert status == 200
        assert body["key"] == "chat-1"
        assert existing.acp_backend == ACP_BACKEND_CODEX

    @pytest.mark.asyncio
    async def test_an_unbound_existing_slot_is_judged_on_the_configured_default(self, tmp_path):
        """An unbound slot's next session is created on the configured default,
        so asking for that default is asking for nothing new."""
        state = _make_state(tmp_path)
        state.get_or_create_slot("chat-1")

        with _configured(ACP_BACKEND_CODEX), _eager_spawn_probe():
            status, _body = await _create(
                _make_app(state), {"name": "chat-1", "backend": ACP_BACKEND_CODEX}
            )

        assert status == 200


class TestForkingOntoAHarness:
    def _source(self, state):
        slot = state.get_or_create_slot("chat-src")
        slot.append("user", "hello", "msg msg-u", broadcast=False)
        slot.append("assistant", "hi", "msg msg-a", broadcast=False)
        return slot

    @pytest.mark.asyncio
    async def test_a_fork_records_the_pick_before_its_transcript_is_saved(self, tmp_path):
        state = _make_state(tmp_path)
        self._source(state)

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _fork(_make_app(state), "chat-src", {"backend": ACP_BACKEND_CODEX})

        assert status == 200, body
        fork = state._slots[body["key"]]
        assert fork.acp_backend == ACP_BACKEND_CODEX
        assert fork._backend_explicit is True

    @pytest.mark.asyncio
    async def test_a_fork_without_a_pick_keeps_todays_behaviour(self, tmp_path):
        state = _make_state(tmp_path)
        source = self._source(state)
        source.acp_backend = ACP_BACKEND_CODEX

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _fork(_make_app(state), "chat-src", {})

        assert status == 200, body
        # Unbound: the server does not infer a harness the caller did not name.
        assert state._slots[body["key"]].acp_backend is None

    @pytest.mark.asyncio
    async def test_a_fork_refuses_a_pick_the_dashboard_does_not_offer(self, tmp_path):
        state = _make_state(tmp_path)
        self._source(state)
        before = set(state._slots)

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _fork(_make_app(state), "chat-src", {"backend": ACP_BACKEND_KAS})

        assert status == 400
        assert body["code"] == "backend_not_selectable"
        assert set(state._slots) == before


class TestAPickSurvivesAnUnclaimedTeardown:
    def test_an_explicit_binding_is_kept_when_the_speculative_session_goes(self, tmp_path):
        """The eager spawn creates a session on the pick; if that session is
        recycled before the first turn, the pick must still be there for the
        next spawn — otherwise the user's choice is silently undone by a timer."""
        state = _make_state(tmp_path)
        slot = state.get_or_create_slot("chat-1")
        slot.acp_backend = ACP_BACKEND_CODEX
        slot._backend_explicit = True
        state.push_slots_update = MagicMock()

        state.wire_session_recycle_callback()
        callback = state.sessions.set_provider_unbound_callback.call_args.args[0]
        with _configured(ACP_BACKEND_KIRO):
            callback("dashboard:chat-1")

        assert slot.acp_backend == ACP_BACKEND_CODEX

    def test_a_speculative_binding_still_follows_the_default(self, tmp_path):
        """The presumption stays right for the case it was written for."""
        state = _make_state(tmp_path)
        slot = state.get_or_create_slot("chat-1")
        slot.acp_backend = ACP_BACKEND_CODEX
        state.push_slots_update = MagicMock()

        state.wire_session_recycle_callback()
        callback = state.sessions.set_provider_unbound_callback.call_args.args[0]
        with _configured(ACP_BACKEND_KIRO):
            callback("dashboard:chat-1")

        assert slot.acp_backend is None
