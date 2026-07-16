from __future__ import annotations

import logging
from collections.abc import Iterable

from kirana_agent.agent.runtime import StoreAgentRuntime
from kirana_agent.artifacts.deck import SalesDeckGenerator
from kirana_agent.artifacts.invoice import InvoiceGenerator
from kirana_agent.config import Settings
from kirana_agent.db import Database
from kirana_agent.domain.service import StoreService
from kirana_agent.seed import seed_database
from kirana_agent.telegram_bot import KiranaTelegramBot


class SecretRedactingFormatter(logging.Formatter):
    """Redact configured credentials from fully rendered logs and tracebacks."""

    def __init__(self, fmt: str, *, secrets: Iterable[str]):
        super().__init__(fmt)
        self._secrets = tuple(secret for secret in secrets if secret)

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        for secret in self._secrets:
            rendered = rendered.replace(secret, "[REDACTED]")
        return rendered


def main() -> None:
    settings = Settings()
    settings.ensure_runtime_secrets()
    settings.ensure_telegram_access_policy()
    log_format = "%(asctime)s %(levelname)s %(name)s %(message)s"
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=log_format,
    )
    redacting_formatter = SecretRedactingFormatter(
        log_format,
        secrets=(settings.openai_api_key, settings.telegram_bot_token),
    )
    for handler in logging.getLogger().handlers:
        handler.setFormatter(redacting_formatter)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if settings.allow_all_telegram_users:
        logging.getLogger(__name__).warning(
            "ALLOW_ALL_TELEGRAM_USERS is enabled: every Telegram user has full store access"
        )
    seed_database(settings.database_path)
    database = Database(settings.database_path)
    service = StoreService(database, timezone=settings.store_timezone)
    invoice_generator = InvoiceGenerator(service, settings.artifact_output_dir)
    deck_generator = SalesDeckGenerator(service, settings.artifact_output_dir)
    runtime = StoreAgentRuntime(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        session_database_path=settings.agent_session_database_path,
    )
    bot = KiranaTelegramBot(
        token=settings.telegram_bot_token,
        allow_all_users=settings.allow_all_telegram_users,
        authorized_user_ids=settings.authorized_telegram_user_ids,
        service=service,
        runtime=runtime,
        invoice_generator=invoice_generator,
        deck_generator=deck_generator,
    )
    bot.run()


if __name__ == "__main__":
    main()
