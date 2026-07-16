from types import SimpleNamespace

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
