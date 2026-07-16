from __future__ import annotations

import pytest

from kirana_agent.config import Settings


def test_authorized_telegram_user_ids_accepts_one_env_id(monkeypatch) -> None:
    monkeypatch.setenv("AUTHORIZED_TELEGRAM_USER_IDS", "123456789")

    settings = Settings(_env_file=None)

    assert settings.authorized_telegram_user_ids == (123456789,)


def test_authorized_telegram_user_ids_accepts_comma_separated_env_ids(monkeypatch) -> None:
    monkeypatch.setenv("AUTHORIZED_TELEGRAM_USER_IDS", "123456789, 987654321")

    settings = Settings(_env_file=None)

    assert settings.authorized_telegram_user_ids == (123456789, 987654321)


def test_allow_all_telegram_users_defaults_to_false(monkeypatch) -> None:
    monkeypatch.delenv("ALLOW_ALL_TELEGRAM_USERS", raising=False)

    settings = Settings(_env_file=None)

    assert settings.allow_all_telegram_users is False


def test_allow_all_telegram_users_accepts_true_from_env(monkeypatch) -> None:
    monkeypatch.setenv("ALLOW_ALL_TELEGRAM_USERS", "true")

    settings = Settings(_env_file=None)

    assert settings.allow_all_telegram_users is True


def test_allow_all_telegram_users_accepts_false_from_env(monkeypatch) -> None:
    monkeypatch.setenv("ALLOW_ALL_TELEGRAM_USERS", "false")

    settings = Settings(_env_file=None)

    assert settings.allow_all_telegram_users is False


def test_allowlist_is_required_when_public_access_is_off() -> None:
    settings = Settings(
        _env_file=None,
        allow_all_telegram_users=False,
        authorized_telegram_user_ids=(),
    )

    with pytest.raises(RuntimeError, match="AUTHORIZED_TELEGRAM_USER_IDS"):
        settings.ensure_telegram_access_policy()


def test_empty_allowlist_is_valid_when_public_access_is_on() -> None:
    settings = Settings(
        _env_file=None,
        allow_all_telegram_users=True,
        authorized_telegram_user_ids=(),
    )

    settings.ensure_telegram_access_policy()


def test_populated_allowlist_is_valid_when_public_access_is_off() -> None:
    settings = Settings(
        _env_file=None,
        allow_all_telegram_users=False,
        authorized_telegram_user_ids=(123456789,),
    )

    settings.ensure_telegram_access_policy()
