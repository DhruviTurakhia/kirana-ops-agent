# Security

Report a vulnerability privately to the repository owner rather than opening a public issue.

Never commit or paste an OpenAI API key, Telegram bot token, real GSTIN, customer phone number, Khata entry, or store database into an issue or pull request. By default, the bot denies access unless the Telegram user ID is explicitly allowlisted. Rotate any secret immediately if it is exposed.

Keep `ALLOW_ALL_TELEGRAM_USERS=false` for normal operation. Setting it to `true` gives every identified Telegram sender full access to inventory, billing, Khata, reports, shared store data, and OpenAI API spend. Public mode has no automatic timeout or rate limit; use it only for a short supervised demo, prefer private chats, then set it back to `false` and restart the worker.

The bundled catalog and generated demo artifacts contain fictional operational data. A real deployment should use encrypted backups, a managed secret store, restricted persistent storage, and a PostgreSQL-backed inbox/outbox architecture before multiple workers are enabled.
