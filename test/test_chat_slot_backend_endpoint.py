"""Choosing the ACP harness a chat slot runs on.

``slot.acp_backend`` is durable — recorded on the slot, restored on rehydration,
kept across an unbind, and passed to the spawn — but until this route existed
nothing could WRITE it on purpose. The only way to run two concurrent sessions on
two harnesses was to flip the global ``agent.acp_backend`` between spawns, which
is a setting about future sessions being used as a per-session control.

What decides whether the route is correct:

**It cannot move a live conversation.** A native session id belongs to the
process that issued it and the model vocabularies on either side are disjoint, so
there is no ``live_session`` scope: a slot that HAS a conversation gets that
conversation discarded and replayed, and is told so. A slot that has none is the
cheap case and nothing is torn down.

**It is not a wider door than the settings page.** Validation is against the
dashboard's own three public choices, never ``ACP_BACKENDS_SELECTABLE``, which
also carries edition-only harnesses an operator may persist by hand.

**A model pin is dropped, never translated.** Vocabularies are disjoint, so a pin
the target cannot run is dropped to "inherit what that harness serves" — and
because the user chose it, the response says so rather than removing it silently.
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from kiro_crew.acp.types import (
    ACP_BACKEND_CLAUDE,
    ACP_BACKEND_CODEX,
    ACP_BACKEND_KAS,
    ACP_BACKEND_KIRO,
    ACP_BACKENDS_DASHBOARD_SELECTABLE,
    ACP_BACKENDS_SELECTABLE,
)
from kiro_crew.dashboard.chat_handlers import api_chat_slot_backend
from kiro_crew.dashboard.state import DashboardState, _ChatSlot

OWNER = "acme-app"
OTHER = "other-app"

# Allowed on the claude harness only, so it is the pin a switch must drop.
CLAUDE_ONLY_MODEL = "opus-4.8-1m"
# Served by every harness, so it is the pin a switch must leave alone.
PORTABLE_MODEL = "claude-opus-4.8"


def _make_app(state: DashboardState, *, declared_app: str = "") -> web.Application:
    app = web.Application()
    app["state"] = state

    @web.middleware
    async def _publish_app(request: web.Request, handler):
        # Stands in for the token middleware, which publishes the validated app
        # token's name. Empty for a dashboard user.
        request["app"] = declared_app
        return await handler(request)

    app.middlewares.append(_publish_app)
    app.router.add_post("/api/chat/slots/{slot}/backend", api_chat_slot_backend)
    return app


def _state(
    *slots: _ChatSlot,
    subagents: list | None = None,
    active_turn: bool | None = None,
    resumable: bool = False,
) -> DashboardState:
    state = MagicMock(spec=DashboardState)
    state._slots = {s.key: s for s in slots}
    state.sessions = MagicMock()
    state.sessions.discard_conversation = AsyncMock()
    if active_turn is None:
        # No live provider, which is what a slot restored from history (or one
        # that never started) looks like: the turn probe has nothing to ask.
        state.sessions.get_provider = MagicMock(return_value=None)
    else:
        provider = MagicMock()
        provider.has_active_turn = MagicMock(return_value=active_turn)
        state.sessions.get_provider = MagicMock(return_value=provider)
    # Explicit, because an auto-created mock attribute answers truthy and would
    # make every slot look as if it held a resumable conversation.
    state.sessions.resumable_hint = MagicMock(return_value=resumable)
    if subagents is None:
        state.subagents = None
    else:
        subs = MagicMock()
        subs.running_agents_for = MagicMock(return_value=subagents)
        subs._queued_depth = MagicMock(return_value=0)
        state.subagents = subs
    return state


def _slot(
    key: str,
    *,
    app: str = "",
    running: bool = False,
    linked: str = "",
    backend: str | None = None,
    model: str = "",
    messages: bool = False,
) -> _ChatSlot:
    slot = _ChatSlot(key)
    slot._app = app
    slot.acp_backend = backend
    slot.model = model
    if linked:
        slot.linked_session_key = linked
    if running:
        # ``running`` is derived from the live task, which is what the route reads.
        slot.task = MagicMock(done=MagicMock(return_value=False))
    if messages:
        slot.append("user", "hello", "msg msg-u", broadcast=False)
        slot.append("assistant", "hi", "msg msg-a", broadcast=False)
        slot._dirty = False
    return slot


@contextlib.contextmanager
def _configured(backend: str):
    """Pin ``agent.acp_backend`` for the duration of one call."""
    cfg = MagicMock()
    cfg.agent.acp_backend = backend
    with patch("kiro_crew.dashboard.chat_handlers.KiroCrewConfig.load", return_value=cfg):
        yield


async def _post(app: web.Application, slot: str, payload=None, *, raw: bool = False):
    async with TestClient(TestServer(app)) as client:
        if raw:
            resp = await client.post(
                f"/api/chat/slots/{slot}/backend",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
        else:
            resp = await client.post(f"/api/chat/slots/{slot}/backend", json=payload)
        return resp.status, await resp.json()


class TestASlotWithNothingToEnd:
    """The cheap case: record the binding and tear nothing down."""

    @pytest.mark.asyncio
    async def test_an_unbound_empty_slot_records_the_binding(self):
        slot = _slot("chat-1-foo")
        state = _state(slot)

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        assert status == 200
        assert body == {
            "ok": True,
            "slot": "chat-1-foo",
            "backend": ACP_BACKEND_CODEX,
            "changed": True,
            "reset": False,
            "model_cleared": False,
            "model": "",
        }
        assert slot.acp_backend == ACP_BACKEND_CODEX
        state.sessions.discard_conversation.assert_not_awaited()
        state.push_slots_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_it_marks_the_slot_dirty_so_a_restart_keeps_the_choice(self):
        """The binding rides the transcript metadata line, and the periodic save
        skips a clean slot — so without this a restart before the first turn
        rehydrates the harness this call just left."""
        slot = _slot("chat-1-foo")
        state = _state(slot)

        with _configured(ACP_BACKEND_KIRO):
            await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        assert slot._dirty is True

    @pytest.mark.asyncio
    async def test_a_pin_the_target_cannot_run_is_dropped_and_reported(self):
        """Dropped, never substituted — and never silently: the user picked it."""
        slot = _slot("chat-1-foo", model=CLAUDE_ONLY_MODEL)
        state = _state(slot)

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        assert status == 200
        assert body["model_cleared"] is True
        assert body["model"] == ""
        assert slot.model == ""

    @pytest.mark.asyncio
    async def test_a_pin_the_target_can_run_survives(self):
        """The negative half: a switch is not licence to clear every pin."""
        slot = _slot("chat-1-foo", model=PORTABLE_MODEL)
        state = _state(slot)

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        assert status == 200
        assert body["model_cleared"] is False
        assert slot.model == PORTABLE_MODEL

    @pytest.mark.asyncio
    async def test_the_kiro_harness_is_a_value_not_an_omission(self):
        """``""`` IS kiro, so presence of the key is what is required."""
        slot = _slot("chat-1-foo", backend=ACP_BACKEND_CODEX)
        state = _state(slot)

        with _configured(ACP_BACKEND_CODEX):
            status, body = await _post(_make_app(state), "chat-1-foo", {"backend": ""})

        assert status == 200
        assert body["changed"] is True
        assert slot.acp_backend == ACP_BACKEND_KIRO

    @pytest.mark.asyncio
    async def test_picking_the_harness_the_slot_already_runs_on_ends_nothing(self):
        """An unbound slot answers with the configured default, so asking for it
        changes no harness — but the binding is still recorded, which is what
        stops a later change to the global default moving the conversation."""
        slot = _slot("chat-1-foo", messages=True)
        state = _state(slot, resumable=True)

        with _configured(ACP_BACKEND_CODEX):
            status, body = await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        assert status == 200
        assert body["changed"] is False
        assert body["reset"] is False
        assert slot.acp_backend == ACP_BACKEND_CODEX
        state.sessions.discard_conversation.assert_not_awaited()


class TestASlotWithAConversation:
    """A running ACP conversation cannot be moved, so the switch ends it."""

    @pytest.mark.asyncio
    async def test_a_transcript_with_turns_is_discarded_and_replayed(self):
        slot = _slot("chat-1-foo", backend=ACP_BACKEND_CLAUDE, messages=True)
        state = _state(slot)

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        assert status == 200
        assert body["reset"] is True
        assert body["changed"] is True
        state.sessions.discard_conversation.assert_awaited_once_with(
            "dashboard:chat-1-foo", replay=True
        )
        assert slot.acp_backend == ACP_BACKEND_CODEX

    @pytest.mark.asyncio
    async def test_a_live_provider_counts_even_with_no_messages(self):
        """The eager spawn creates a real session before the first message, so an
        empty transcript is not evidence that nothing exists on the old harness."""
        slot = _slot("chat-1-foo", backend=ACP_BACKEND_CLAUDE)
        state = _state(slot, active_turn=False)

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        assert status == 200
        assert body["reset"] is True
        state.sessions.discard_conversation.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_resume_pointer_counts_too(self):
        """After a restart the native session survives with no provider and, for a
        slot whose window was trimmed, no rows either — only the map knows."""
        slot = _slot("chat-1-foo", backend=ACP_BACKEND_CLAUDE)
        state = _state(slot, resumable=True)

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        assert status == 200
        assert body["reset"] is True
        state.sessions.resumable_hint.assert_called_with("dashboard:chat-1-foo")
        state.sessions.discard_conversation.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_binding_is_written_before_the_discard_suspends(self):
        """A turn admitted during that await would otherwise spawn on the OLD
        binding and re-bind it, undoing the switch the caller just asked for."""
        slot = _slot("chat-1-foo", backend=ACP_BACKEND_CLAUDE, messages=True)
        state = _state(slot)
        seen_during_discard: list[str | None] = []

        async def _discard(key, *, replay):
            seen_during_discard.append(slot.acp_backend)

        state.sessions.discard_conversation = AsyncMock(side_effect=_discard)

        with _configured(ACP_BACKEND_KIRO):
            await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        assert seen_during_discard == [ACP_BACKEND_CODEX]

    @pytest.mark.asyncio
    async def test_the_transcript_says_the_harness_changed(self):
        """Not silently under an open conversation: the row is what the reader of
        that transcript has to explain a replay and a fresh session with."""
        slot = _slot("chat-1-foo", backend=ACP_BACKEND_CLAUDE, messages=True)
        state = _state(slot)

        with _configured(ACP_BACKEND_KIRO):
            await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        notice = slot.messages[-1]
        assert notice["role"] == "assistant"
        assert "codex" in notice["content"]
        assert "replaying" in notice["content"]

    @pytest.mark.asyncio
    async def test_the_notice_names_the_model_it_had_to_drop(self):
        slot = _slot(
            "chat-1-foo", backend=ACP_BACKEND_CLAUDE, model=CLAUDE_ONLY_MODEL, messages=True
        )
        state = _state(slot)

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        assert status == 200
        assert body["model_cleared"] is True
        assert CLAUDE_ONLY_MODEL in slot.messages[-1]["content"]

    @pytest.mark.asyncio
    async def test_a_channel_born_slot_ends_its_channel_session(self):
        """A channel-born slot's turns run on the channel's own session, so
        deriving ``dashboard:<slot>`` would discard a key no session ever had —
        the call reports a reset and ends nothing."""
        slot = _slot(
            "slack_123.456", linked="slack:123.456", backend=ACP_BACKEND_CLAUDE, messages=True
        )
        state = _state(slot)

        with _configured(ACP_BACKEND_KIRO):
            status, _ = await _post(_make_app(state), "slack_123.456", {"backend": "codex"})

        assert status == 200
        state.sessions.discard_conversation.assert_awaited_once_with("slack:123.456", replay=True)


class TestItValidatesAgainstTheDashboardsOwnSet:
    def test_the_dashboard_set_is_narrower_than_what_an_operator_may_persist(self):
        """Validating against ``ACP_BACKENDS_SELECTABLE`` would make this route a
        wider door onto an edition-only harness than the settings page is."""
        assert set(ACP_BACKENDS_DASHBOARD_SELECTABLE) < ACP_BACKENDS_SELECTABLE
        assert ACP_BACKEND_KAS not in ACP_BACKENDS_DASHBOARD_SELECTABLE

    def test_the_settings_enum_reads_the_same_set(self):
        """One source, so the two surfaces cannot offer different harnesses."""
        from kiro_crew.dashboard.handlers.core import _EDITABLE_CONFIG

        assert _EDITABLE_CONFIG["agent.acp_backend"]["values"] == list(
            ACP_BACKENDS_DASHBOARD_SELECTABLE
        )

    @pytest.mark.asyncio
    async def test_a_selectable_but_undashboarded_harness_is_refused(self):
        slot = _slot("chat-1-foo")
        state = _state(slot)

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _post(_make_app(state), "chat-1-foo", {"backend": ACP_BACKEND_KAS})

        assert status == 400
        assert body["code"] == "backend_not_selectable"
        assert slot.acp_backend is None

    @pytest.mark.asyncio
    async def test_an_unknown_harness_is_refused_rather_than_resolved(self):
        """An explicit pick is never quietly swapped for something else."""
        slot = _slot("chat-1-foo")
        state = _state(slot)

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _post(_make_app(state), "chat-1-foo", {"backend": "gemini"})

        assert status == 400
        assert body["code"] == "backend_not_selectable"
        assert slot.acp_backend is None

    @pytest.mark.asyncio
    async def test_a_missing_backend_key_is_refused(self):
        state = _state(_slot("chat-1-foo"))

        status, body = await _post(_make_app(state), "chat-1-foo", {})

        assert status == 400
        assert body["code"] == "backend_missing"

    @pytest.mark.asyncio
    async def test_a_non_string_backend_is_refused(self):
        state = _state(_slot("chat-1-foo"))

        status, body = await _post(_make_app(state), "chat-1-foo", {"backend": 3})

        assert status == 400
        assert body["code"] == "backend_invalid"

    @pytest.mark.asyncio
    async def test_a_malformed_body_is_refused_with_a_code(self):
        state = _state(_slot("chat-1-foo"))

        status, body = await _post(_make_app(state), "chat-1-foo", "{not json", raw=True)

        assert status == 400
        assert body["code"] == "invalid_json"


class TestAStoredValueDegradesRatherThanRaising:
    @pytest.mark.asyncio
    async def test_a_binding_that_is_no_longer_selectable_does_not_strand_the_slot(self):
        """An uninstalled harness (or one lost to an edition change) leaves a
        recorded value nothing serves. It degrades to the configured default with
        the reason logged — the same rule as ``_normalize_acp_backend`` — so the
        slot can still be moved somewhere else instead of raising."""
        slot = _slot("chat-1-foo", backend="retired-harness", messages=True)
        state = _state(slot)

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        assert status == 200
        assert body["changed"] is True
        assert slot.acp_backend == ACP_BACKEND_CODEX

    @pytest.mark.asyncio
    async def test_a_degraded_binding_compares_against_the_default_it_fell_back_to(self):
        """Its next session would be created on the configured default, so asking
        for that default changes no harness — but the stale value is still
        replaced, so a later change to the default cannot move the conversation."""
        slot = _slot("chat-1-foo", backend="retired-harness", messages=True)
        state = _state(slot)

        with _configured(ACP_BACKEND_CODEX):
            status, body = await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        assert status == 200
        assert body["changed"] is False
        assert body["reset"] is False
        assert slot.acp_backend == ACP_BACKEND_CODEX
        state.sessions.discard_conversation.assert_not_awaited()


class TestItRefusesWhenItCannotBeSafe:
    """Every guard runs whether or not this particular slot has a conversation:
    whether it does is not the caller's to know, so the answer must not depend on
    it."""

    @pytest.mark.asyncio
    async def test_an_unknown_slot_is_not_found(self):
        state = _state(_slot("chat-1-foo"))

        status, body = await _post(_make_app(state), "chat-9-nope", {"backend": "codex"})

        assert status == 404
        assert body["code"] == "slot_not_found"

    @pytest.mark.asyncio
    async def test_a_running_turn_blocks_the_switch(self):
        slot = _slot("chat-1-foo", running=True, backend=ACP_BACKEND_CLAUDE, messages=True)
        state = _state(slot)

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        assert status == 409
        assert body["code"] == "turn_in_flight"
        assert slot.acp_backend == ACP_BACKEND_CLAUDE
        state.sessions.discard_conversation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_turn_in_flight_on_the_session_blocks_the_switch(self):
        """An inbound channel message runs a turn on the linked SESSION with no
        dashboard task behind it, so ``slot.running`` stays False."""
        slot = _slot(
            "slack_123.456", linked="slack:123.456", backend=ACP_BACKEND_CLAUDE, messages=True
        )
        state = _state(slot, active_turn=True)

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _post(_make_app(state), "slack_123.456", {"backend": "codex"})

        assert status == 409
        assert body["code"] == "turn_in_flight"
        state.sessions.discard_conversation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_plan_between_stages_blocks_the_switch(self):
        """``running`` reads False BETWEEN an autopilot plan's stages while the
        plan is still mid-flight."""
        slot = _slot("chat-1-foo", backend=ACP_BACKEND_CLAUDE, messages=True)
        slot._in_stage_execution = True
        state = _state(slot)

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        assert status == 409
        assert body["code"] == "slot_orchestrating"

    @pytest.mark.asyncio
    async def test_attached_sub_agents_block_the_switch(self):
        """The teardown releases the shared runtime the parent's children run on,
        and ``running`` is False while they keep going."""
        slot = _slot("chat-1-foo", backend=ACP_BACKEND_CLAUDE, messages=True)
        state = _state(slot, subagents=[{"id": "sub-1"}])

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        assert status == 409
        assert body["code"] == "slot_subagents_running"

    @pytest.mark.asyncio
    async def test_a_stop_in_progress_blocks_the_switch(self):
        """A teardown is already under way on this slot; a second one racing it
        would decide the harness by whichever finished last."""
        slot = _slot("chat-1-foo", backend=ACP_BACKEND_CLAUDE, messages=True)
        slot._stop_state = "soft_pending"
        state = _state(slot)

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        assert status == 409
        assert body["code"] == "slot_stopping"

    @pytest.mark.asyncio
    async def test_a_pending_approval_blocks_the_switch(self):
        """The card belongs to the session this would discard, so answering it
        would reach an agent that no longer exists."""
        import asyncio

        slot = _slot("chat-1-foo", backend=ACP_BACKEND_CLAUDE, messages=True)
        slot._approval_futures["req-1"] = asyncio.get_running_loop().create_future()
        state = _state(slot)

        with _configured(ACP_BACKEND_KIRO):
            status, body = await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        assert status == 409
        assert body["code"] == "slot_approval_pending"
        slot._approval_futures["req-1"].cancel()

    @pytest.mark.asyncio
    async def test_a_settled_slot_with_a_provider_does_not_block(self):
        """The negative half: having a provider is not being busy."""
        slot = _slot("chat-1-foo", backend=ACP_BACKEND_CLAUDE, messages=True)
        state = _state(slot, active_turn=False)

        with _configured(ACP_BACKEND_KIRO):
            status, _ = await _post(_make_app(state), "chat-1-foo", {"backend": "codex"})

        assert status == 200


class TestAppScope:
    """Authorization is on the SESSION this may discard, not on the slot it was
    reached through — the same shared policy the sibling teardown route uses."""

    @pytest.mark.asyncio
    async def test_an_app_may_switch_a_slot_it_created(self):
        state = _state(_slot("acme-obj-1", app=OWNER))

        with _configured(ACP_BACKEND_KIRO):
            status, _ = await _post(
                _make_app(state, declared_app=OWNER), "acme-obj-1", {"backend": "codex"}
            )

        assert status == 200

    @pytest.mark.asyncio
    async def test_an_app_cannot_switch_another_apps_slot(self):
        slot = _slot("acme-obj-1", app=OWNER)
        state = _state(slot)

        status, body = await _post(
            _make_app(state, declared_app=OTHER), "acme-obj-1", {"backend": "codex"}
        )

        # 404, not 403: a foreign slot must be indistinguishable from a missing
        # one, or the error itself enumerates other apps' slots (CWE-204).
        assert status == 404
        assert body["code"] == "slot_not_found"
        assert slot.acp_backend is None

    @pytest.mark.asyncio
    async def test_an_app_cannot_switch_an_unscoped_slot(self):
        slot = _slot("chat-1-mine")
        state = _state(slot)

        status, body = await _post(
            _make_app(state, declared_app=OWNER), "chat-1-mine", {"backend": "codex"}
        )

        assert status == 404
        assert slot.acp_backend is None

    @pytest.mark.asyncio
    async def test_an_app_cannot_switch_a_channel_session_its_slot_is_bound_to(self):
        """Owning the slot is not owning the session: ``get_or_create_slot``
        resolves ``linked_session_key`` for a name shaped like a channel stem, so
        ownership alone would turn that binding into capability escalation."""
        slot = _slot("slack_123.456", app=OWNER, linked="slack:123.456")
        state = _state(slot)

        status, body = await _post(
            _make_app(state, declared_app=OWNER), "slack_123.456", {"backend": "codex"}
        )

        assert status == 404
        assert body["code"] == "slot_not_found"
        assert slot.acp_backend is None

    @pytest.mark.asyncio
    async def test_a_dashboard_user_may_switch_an_app_owned_slot(self):
        state = _state(_slot("acme-obj-1", app=OWNER))

        with _configured(ACP_BACKEND_KIRO):
            status, _ = await _post(
                _make_app(state, declared_app=""), "acme-obj-1", {"backend": "codex"}
            )

        assert status == 200


class TestThereIsNoLiveSessionScope:
    def test_the_route_takes_no_scope(self):
        """A running ACP conversation cannot be moved between harnesses: the
        native session id belongs to the process that issued it. A scope that
        promised to leave the session alone would have to lie."""
        import inspect

        from kiro_crew.dashboard import chat_handlers

        src = inspect.getsource(chat_handlers.api_chat_slot_backend)
        assert "SCOPE_LIVE_SESSION" not in src
        assert '"scope"' not in src

    def test_the_body_is_read_before_the_busy_guards(self):
        """Source-order guard: reading the body must not widen the teardown race.

        ``await request.json()`` is a suspension whose duration the CLIENT
        controls, and every busy guard below it protects work that can START
        during a suspension. Asserted on the source rather than on behaviour
        because the failure is an interleaving — a test that posts a slow body and
        races a concurrent turn would be exactly the timing-dependent flake the
        testing conventions forbid, and it would pass on a fast machine with the
        bug present.
        """
        import inspect

        from kiro_crew.dashboard import chat_handlers

        src = inspect.getsource(chat_handlers.api_chat_slot_backend)
        body_read = src.index("await request.json()")
        first_guard = src.index("_slot_teardown_denied(")
        teardown = src.index("_restart_slot_conversation(")

        assert body_read < first_guard
        assert first_guard < teardown
