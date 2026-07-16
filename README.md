# Kirana Ops Agent

A Telegram-first operations agent for a small Indian supermarket. The owner speaks naturally; an OpenAI model decides which typed business tools to call. There is no intent router, command menu, web app, or admin panel.

- **Public repository:** [DhruviTurakhia/kirana-ops-agent](https://github.com/DhruviTurakhia/kirana-ops-agent)
- **Telegram bot:** [@dhruvi_ai_shop_bot](https://t.me/dhruvi_ai_shop_bot) — username reserved; deployment remains pending until rotated secrets are configured.
- **Plain-English guide:** [How the application works](docs/HOW_IT_WORKS.md) — a 5–10 minute explanation for non-technical readers.

## What works

- Receive stock, search the 120-SKU demo catalog, inspect inventory, and identify low stock.
- Build a bill across messages, edit it, preview its GST breakup, and explicitly finalize it.
- Refuse overselling, below-cost sales, above-MRP sales, stale drafts, invalid units, and missing payment references at the transactional tool layer.
- Record Cash, UPI, Card, or Khata sales; charge and settle customer Khata balances.
- Persist stock, bills, customers, drafts, owner preferences, and conversation sessions in SQLite.
- Generate a polished GST invoice PDF and a six-slide editable PowerPoint with real charts.
- Remember preferences across `/new`, while `/new` rotates only conversational context.
- Deduplicate Telegram updates and replay exact mutation results without double-decrementing stock.

## Why this harness

The project uses the [OpenAI Agents SDK for Python](https://openai.github.io/openai-agents-python/). Its `Agent` + `Runner` loop uses the Responses API by default and repeatedly reasons, calls local function tools, observes structured results, and continues until it has a user-facing answer. The SDK also provides durable sessions and tracing. This is a direct fit for an agent that composes inventory, billing, Khata, memory, analytics, and artifact tools; it avoids turning shopkeeper language into a hardcoded intent state machine.

`OPENAI_MODEL` defaults to `gpt-5.6-terra`, the current balanced GPT-5.6 tier, and remains configurable for the reviewer’s API project. See the official [model catalog](https://developers.openai.com/api/docs/models) and [Agents SDK tools guide](https://openai.github.io/openai-agents-python/tools/).

## Control loop and safety

Natural-language input always reaches the model. The model can search for grounded product/customer IDs, then call semantic tools such as `patch_bill_draft`, `finalize_bill`, `record_khata_payment`, and `generate_invoice_pdf`. Prices, HSN, GST, costs, and stock are fetched inside those tools; the model never supplies them when billing.

All money is integer paise, all tax rates are basis points, and quantities are canonical integer atoms (grams, millilitres, or thousandths of a count unit). Finalization runs inside `BEGIN IMMEDIATE`, revalidates draft revision, price/tax version, cost, MRP, and stock, inserts immutable bill snapshots and ledgers, decrements inventory, assigns the invoice number, and posts a Khata debit together. A unique bill per draft, stock-movement keys, per-operation idempotency records, and Telegram update records prevent double mutations. SQLite WAL is suitable for the local demo; [the architecture note](docs/ARCHITECTURE.md) describes the PostgreSQL/webhook/outbox production path.

GST prices are tax-inclusive. Each line extracts taxable value with `gross × 10000 / (10000 + rate_bps)`, rounds half-up to paise, and deterministically splits intra-state tax into CGST and SGST. Final headers are sums of stored lines. Products reference versioned, packaging-aware tax rules: loose does **not** automatically mean nil-rated. See [GST and demo data](docs/GST_AND_DEMO_DATA.md).

## Run locally

Prerequisites: Python 3.11+, [`uv`](https://docs.astral.sh/uv/), an OpenAI API key, and a Telegram bot token.

```powershell
uv sync --extra dev
Copy-Item .env.example .env
# Fill the API keys. Keep ALLOW_ALL_TELEGRAM_USERS=false and add your numeric ID.
uv run kirana-seed
uv run kirana-bot
```

Run only one bot process for a Telegram token. In locked mode, an unauthorized user can send
`/start`; the private-bot reply shows the numeric ID to add to `AUTHORIZED_TELEGRAM_USER_IDS`.
Restart the bot after changing the allowlist.

For a short public demo window, keep the normal allowlist saved, set `ALLOW_ALL_TELEGRAM_USERS=true`, and restart the bot. **Every Telegram user then has full store access and can spend OpenAI credits.** Set it back to `false` and restart immediately after the demo; the setting is not timed or hot-reloaded.

The OpenAI API key must come from an API project; a ChatGPT subscription does not automatically expose a reusable API key. Never commit `.env`.

Run verification:

```powershell
uv run pytest
uv run ruff check .
```

The deterministic offline demo creates sample bills, Khata activity, a PDF, and a PPTX without calling OpenAI:

```powershell
uv run kirana-demo
```

## Launch checklist

Only the account owner can complete these external steps:

1. Create an OpenAI API project/key and place it in the deployment secret `OPENAI_API_KEY`.
2. Ask Telegram `@BotFather` for a bot, set `TELEGRAM_BOT_TOKEN`, keep `ALLOW_ALL_TELEGRAM_USERS=false`, and add your numeric user ID to `AUTHORIZED_TELEGRAM_USER_IDS`.
3. Deploy the Docker worker to a service that supports a continuously running background process; set the same secrets and attach persistent storage at `/app/data` and `/app/output`.
4. Keep the worker running during review and verify [@dhruvi_ai_shop_bot](https://t.me/dhruvi_ai_shop_bot) follows the selected access mode. Locked allowlist mode is the production default.
5. Before every push, confirm `.env`, database files, real GSTINs, customer data, and generated customer artifacts remain absent from GitHub.

For the requested 4–5 minute recording: receive stock → start a multi-item draft → remove butter and change Maggi to six → try to sell ten Maggi and show the tool refusal → finalize a valid bill → charge/pay Khata → request the invoice PDF and weekly deck → set default UPI → `/new` → show the preference is retained.

## Scope and data notice

The 120 product names and pack sizes are familiar demo labels. Costs, prices, inventory, and reorder levels are fictional illustrative data—not live market claims. GST/HSN metadata is a curated official-source snapshot, not tax advice or a binding classification. Verify current CBIC/GST Council notifications, product composition, packaging condition, and the supplier invoice before production use. Generated invoices are watermarked `DEMO · NOT A TAX INVOICE` until a seller GSTIN is configured.

Detailed requirements and evidence are in [Assignment](docs/ASSIGNMENT.md), [Architecture](docs/ARCHITECTURE.md), [GST and demo data](docs/GST_AND_DEMO_DATA.md), and [Test matrix](docs/TEST_MATRIX.md). Follow the point-by-point [deployment guide](docs/DEPLOYMENT.md) and exact [4–5 minute recording guide](docs/RECORDING_GUIDE.md) for submission.
