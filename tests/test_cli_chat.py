"""Regression tests for the synchronous CLI chat boundary."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from kiro_crew import cli_chat


def test_run_chat_warms_backend_before_entering_event_loop() -> None:
    """The sync CLI must warm sandbox detection before asyncio.run()."""
    fake_chat = AsyncMock()

    with (
        patch.object(cli_chat, "warm_backend", create=True) as warm_backend,
        patch.object(cli_chat, "_chat", fake_chat),
    ):
        cli_chat._run_chat("hello", None, agent="codex-cli")

    warm_backend.assert_called_once_with()
    fake_chat.assert_awaited_once_with("hello", None, agent="codex-cli")
