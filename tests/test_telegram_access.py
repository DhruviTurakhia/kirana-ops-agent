from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from telegram.error import Conflict

from kirana_agent.telegram_bot import KiranaTelegramBot


def _bot(*, allow_all_users: bool, authorized_user_ids: set[int]) -> KiranaTelegramBot:
    bot = object.__new__(KiranaTelegramBot)
    bot.allow_all_users = allow_all_users
    bot.authorized_user_ids = frozenset(authorized_user_ids)
    return bot


def _update(user_id: int | None) -> SimpleNamespace:
    user = None if user_id is None else SimpleNamespace(id=user_id)
    return SimpleNamespace(effective_user=user)


def test_public_mode_accepts_any_identified_telegram_user() -> None:
    bot = _bot(allow_all_users=True, authorized_user_ids=set())

    assert bot._authorized(_update(111)) is True
    assert bot._authorized(_update(999)) is True


def test_allowlist_mode_accepts_only_configured_users() -> None:
    bot = _bot(allow_all_users=False, authorized_user_ids={111})

    assert bot._authorized(_update(111)) is True
    assert bot._authorized(_update(999)) is False


def test_public_mode_still_requires_a_telegram_user() -> None:
    bot = _bot(allow_all_users=True, authorized_user_ids=set())

    assert bot._authorized(_update(None)) is False


async def test_private_mode_rejection_reports_the_senders_numeric_id() -> None:
    bot = _bot(allow_all_users=False, authorized_user_ids={111})
    reply_text = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=987654321),
        effective_message=SimpleNamespace(reply_text=reply_text),
    )

    await bot._reject_unauthorized(update)

    reply_text.assert_awaited_once()
    response = reply_text.await_args.args[0]
    assert "987654321" in response
    assert "AUTHORIZED_TELEGRAM_USER_IDS" in response


async def test_private_mode_rejection_handles_an_update_without_a_user() -> None:
    bot = _bot(allow_all_users=False, authorized_user_ids={111})
    reply_text = AsyncMock()
    update = SimpleNamespace(
        effective_user=None,
        effective_message=SimpleNamespace(reply_text=reply_text),
    )

    await bot._reject_unauthorized(update)

    reply_text.assert_awaited_once_with(
        "This bot is private. Ask the store owner to authorize your account."
    )


async def test_polling_conflict_stops_the_duplicate_application() -> None:
    bot = _bot(allow_all_users=False, authorized_user_ids={111})
    bot.application = SimpleNamespace(stop_running=Mock())
    context = SimpleNamespace(error=Conflict("terminated by other getUpdates request"))

    await bot.on_error(None, context)

    bot.application.stop_running.assert_called_once_with()


async def test_non_conflict_error_does_not_stop_the_application() -> None:
    bot = _bot(allow_all_users=False, authorized_user_ids={111})
    bot.application = SimpleNamespace(stop_running=Mock())
    context = SimpleNamespace(error=RuntimeError("unexpected"))

    await bot.on_error(None, context)

    bot.application.stop_running.assert_not_called()
