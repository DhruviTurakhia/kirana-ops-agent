from __future__ import annotations

import logging

from kirana_agent.main import SecretRedactingFormatter


def test_log_formatter_redacts_credentials_after_rendering() -> None:
    formatter = SecretRedactingFormatter(
        "%(levelname)s %(message)s",
        secrets=("openai-test-secret", "telegram-test-secret"),
    )
    record = logging.LogRecord(
        name="security-test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="request %s failed with %s",
        args=("telegram-test-secret", "openai-test-secret"),
        exc_info=None,
    )

    rendered = formatter.format(record)

    assert "telegram-test-secret" not in rendered
    assert "openai-test-secret" not in rendered
    assert rendered.count("[REDACTED]") == 2
