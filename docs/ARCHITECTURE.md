# Architecture

## Outcome and scope

The MVP is a local-first Python application in which an OpenAI agent operates one Indian kirana store through Telegram. The model interprets natural language and orchestrates typed tools; deterministic Python domain services own money, GST, stock, billing, khata, idempotency, and artifact generation.

The local reference deployment uses SQLite and local artifact storage. The production path replaces SQLite with PostgreSQL and local files with durable object storage without changing agent tools or domain behavior.

## Chosen stack

| Concern | MVP | Production path |
| --- | --- | --- |
| Language | Python 3.11+ | Same |
| Agent harness | OpenAI Agents SDK using function tools and durable sessions | Same |
| Model API | OpenAI Responses API through the Agents SDK; model selected by configuration | Same |
| Telegram | Bot API; long polling for local development | HTTPS webhook with a secret token |
| Persistence | SQLite in WAL mode, foreign keys enabled | PostgreSQL |
| Data access | Repository/domain-service boundary with explicit transactions | Same boundary with PostgreSQL locking |
| PDF | Deterministic invoice renderer | Same; generated files stored in object storage |
| PPTX | `python-pptx` with charts backed by queried data | Same |
| Tests | `pytest`, property tests, concurrency tests, artifact inspection | Same against a real PostgreSQL test database |

The OpenAI Agents SDK supplies the model/tool control loop, typed function tools, session support, guardrails, usage tracking, and traces. Business correctness does not depend on a model instruction being obeyed.

## Component flow

```text
Telegram update
    │
    ▼
Transport adapter ──► durable update inbox / duplicate check
    │
    ▼
Store Operations Agent ──► read tools ──► repositories
    │                         │
    │                         └──────────► structured facts/errors
    ▼
mutation tools ──► domain services ──► one database transaction
                                      │
                                      ├── business records / ledgers
                                      └── outbound message or artifact event
    │
    ▼
Telegram response / PDF / PPTX
```

Only Telegram transport commands such as `/start`, `/help`, and `/new` bypass natural-language reasoning. All store requests go through the agent. There is no keyword or regex intent router.

## Agent control loop

For every accepted update:

1. Deduplicate the Telegram `update_id` and bind the trusted store, owner, chat, session, and source-update identifiers to the run context.
2. Load the current conversation session and relevant typed preferences.
3. Run one Store Operations agent with the currently enabled tool groups.
4. Let the model search or inspect store data before choosing a mutation.
5. Execute tools and feed structured results back to the model until it can answer, ask a clarification, or report a refusal.
6. Persist the final response and enqueue Telegram delivery.

Read tools may run concurrently. Mutating tools are serialized within a turn. The run has limits on tool calls, elapsed time, and model retries. Tool results use stable error codes so the model can recover without parsing exception prose.

Examples include `AMBIGUOUS_PRODUCT`, `INSUFFICIENT_STOCK`, `STALE_DRAFT`, `PRICE_CHANGED`, `BELOW_COST`, `MISSING_HSN`, `KHATA_NOT_FOUND`, and `IDEMPOTENCY_CONFLICT`.

## Skills and tool surface

The project organizes instructions and tools by capability while keeping one coordinating agent. Transactional work is not handed between autonomous agents.

### Catalog and inventory

- `search_products(query)` returns ranked candidates with IDs, units, prices, tax, and stock.
- `create_product(spec)` validates identity, unit model, HSN, slab, price, MRP, and cost requirements.
- `get_inventory(product_ids)` reads current balances.
- `list_low_stock()` compares on-hand quantity with reorder level.
- `receive_stock(lines, reference)` records one atomic receipt and immutable stock movements.

### Billing and GST

- `create_bill_draft(label, customer_id)` starts an open draft.
- `list_open_drafts()` and `get_bill_draft(draft_id)` ground follow-up turns.
- `patch_bill_draft(draft_id, expected_revision, operations)` batches adds, removals, quantity changes, customer changes, and payment changes from one message.
- `prepare_bill_finalize(draft_id, expected_revision)` returns the exact preview and a confirmation token tied to its revision and content hash.
- `finalize_bill(draft_id, confirmation_token)` performs the atomic checkout.
- `lookup_bill(reference)` retrieves a finalized bill.

Product names are resolved before mutation. Draft tools receive product IDs; they never accept model-invented prices, stock values, HSN codes, or tax slabs for an existing product.

### Khata

- `search_customers(query)` returns identity candidates.
- `open_khata(customer_id)` explicitly creates an account.
- `get_khata_balance(account_id)` reads the immutable ledger.
- `record_khata_charge(account_id, amount, reason)` adds a debit.
- `record_khata_payment(account_id, amount, mode, reference)` adds a payment credit.

### Preferences, reports, and artifacts

- `get_relevant_preferences(keys)` reads durable typed defaults.
- `set_preference(key, value)` and `unset_preference(key)` validate and audit standing preferences.
- `get_daily_summary(business_date)` returns an as-of close report.
- `get_sales_analysis(from_date, to_date)` returns chart-ready data.
- `generate_invoice_pdf(bill_id)` renders a finalized immutable bill.
- `generate_sales_deck(from_date, to_date)` renders a PPTX from stored sales and inventory data.

Tools receive `store_id`, `owner_id`, `session_id`, and idempotency metadata from trusted run context, never from model arguments.

## Domain and persistence model

Principal records are:

- Store profile: legal name, GSTIN, state code, timezone, invoice prefix, and invoice counter.
- Product and aliases: SKU, name, packaged/loose status, measurement dimension, base unit, pricing quantum, cost, sell price, MRP, HSN, GST profile, and reorder level.
- Current product stock plus an immutable stock-movement ledger; a receipt is represented by one atomic command and its linked RECEIVE movements.
- Bill drafts and draft lines, each with an optimistic revision.
- Finalized bills and bill-line snapshots with one payment mode and reference per bill.
- Customers and an immutable khata-entry ledger; a customer must exist before payment can be posted.
- Typed owner preferences plus the shared audit log.
- Agent sessions and messages.
- Telegram update inbox records containing the durable response/artifact payload, plus idempotent command records. A dedicated outbox is the production scaling path.
- Artifact metadata containing the source hash, template version, and storage location.

Every business row is scoped to a store. Telegram bindings determine that store before an agent run, preventing the model from selecting another tenant.

## Exact numeric representation

- Money is stored as integer paise.
- GST rates are stored as integer basis points, including `0`, `500`, `1200`, `1800`, and `2800` where present in the reviewed catalog.
- Mass is stored as integer grams, volume as integer millilitres, and discrete goods as integer pieces or packets.
- A price carries its pricing quantum, for example `₹45 per 1,000 g`.
- Calculations use `Decimal` with `ROUND_HALF_UP`; binary floating point is forbidden in business arithmetic.

This representation permits loose quantities without losing precision.

## GST policy

The MVP models intra-state, tax-inclusive B2C retail prices.

For a line with rounded gross value `G` and tax rate `r` in basis points:

```text
taxable = round_half_up(G × 10000 / (10000 + r))
GST     = G - taxable
CGST    = deterministic half of GST
SGST    = GST - CGST
```

If a one-paise residual exists, the implementation assigns it consistently so `CGST + SGST = GST`. Invoice totals are sums of stored, rounded line values; they are never independently recomputed from the header. Zero-rated lines have no CGST or SGST. HSN summaries aggregate the same persisted line values.

HSN and tax profiles come from the catalog. A new product with incomplete tax classification remains inactive for sale until the owner provides the missing data. Tax profiles support effective dates, and finalized lines retain a snapshot so historical invoices cannot change.

## Draft and finalization state machine

```text
OPEN ──edit──► OPEN
  │
  ├──cancel──► CANCELLED
  │
  └──confirmed finalize──► FINALIZED
```

- Draft edits never reserve or decrement stock.
- Each edit requires the caller's `expected_revision`; concurrent edits cannot silently overwrite one another.
- At most one draft is open per Telegram chat. Separate chats can hold concurrent drafts, which still exercises database-level concurrency. Starting another bill in the same chat requires finalizing or cancelling the current one.
- `/new` starts a new conversation session and clears the conversational focus; it does not erase the durable open draft or store memory. Resuming that draft must be explicit after the reset.
- A finalize confirmation token is bound to the draft ID, revision, content hash, total, and expiry. A token becomes invalid after any edit.
- If cost, price, MRP, or tax changes after preview, finalization returns `PRICE_CHANGED` and requires a refreshed preview and confirmation.

Finalization is one transaction:

1. Validate and lock the draft.
2. Load all product and inventory rows in stable product-ID order.
3. Recheck stock, product activity, price snapshot, tax, payment, MRP, and below-cost rules.
4. Allocate a unique invoice number.
5. Insert immutable bill and line snapshots.
6. Decrement inventory and append one SALE movement per line.
7. Record tender allocations and, for a credit sale, its khata debit.
8. Mark the draft finalized and store the idempotent command result.
9. Commit the business records and durable response metadata together.

A database constraint prevents negative inventory. Finalized records are never deleted; future cancellation or return support must use compensating records.

## SQLite concurrency and PostgreSQL path

SQLite is acceptable for the local-first MVP if its limitations are explicit:

- Enable foreign keys, WAL mode, and a busy timeout.
- Use short `BEGIN IMMEDIATE` write transactions so a competing writer waits before it reads mutable balances.
- Apply conditional stock updates and verify the affected row count.
- Keep document rendering and model calls outside write transactions.
- Retry only transient `busy` failures; never retry a domain refusal.

SQLite serializes writers, so it preserves correctness but limits throughput. The production PostgreSQL adapter uses row-level `SELECT … FOR UPDATE` locks in stable order, a non-negative inventory check constraint, and retry handling for serialization or deadlock failures. Tool contracts and domain invariants remain identical.

## Idempotency and Telegram delivery

Idempotency has three independent layers:

1. The update inbox has a unique `(bot_id, telegram_update_id)` key.
2. Each mutation records `(store_id, source_update_id, operation_kind, target_id)`, a canonical argument hash, and its result. An exact retry returns that result; a different payload under the same key returns `IDEMPOTENCY_CONFLICT`.
3. Domain constraints enforce one finalized bill per draft and one stock movement per bill line.

This protects the critical crash case in which a transaction commits but the process dies before replying to Telegram.

Production webhooks validate Telegram's secret-token header and acknowledge only after the update is durably accepted. Outbound delivery retries timeouts, 429 responses using `retry_after`, and transient 5xx failures with capped exponential backoff.

Business mutation is exactly once. Telegram delivery is at least once because an outbound request may succeed while its HTTP response is lost; the design minimizes duplicates but does not claim an impossible exactly-once external-delivery guarantee. A production deployment may split the persisted response payload into a conventional outbox table without changing domain commands.

## Khata invariants

Positive balance means the customer owes the store.

- CHARGE and CREDIT_SALE increase the balance.
- PAYMENT decreases it.
- Ledger entries are immutable and idempotent.
- A payment cannot create a missing account.
- A payment cannot exceed the outstanding balance in the MVP.
- The account is locked while a payment or charge is posted.
- A credit bill and its khata debit commit atomically.

Duplicate customer names require clarification; phone number is the preferred secondary identifier when available.

## Durable memory

Conversation history and business memory are deliberately separate.

- The Agents SDK session preserves recent multi-turn context.
- `/new` creates a fresh session.
- Typed database preferences preserve standing defaults across sessions and restarts.
- Legal shop identity is stored on the store profile, not in free-form conversation memory.
- Product preferences map a generic term such as `atta` to a real product ID.

Precedence is: an explicit instruction in the current message, then a valid persisted preference, then a clarification. A preference pointing to an inactive or deleted product is not silently applied.

## Artifacts and analytics

Invoice PDF generation reads only a finalized bill snapshot and includes seller identity, GSTIN and state, invoice number and date, line descriptions, quantities and units, HSN, taxable values, GST rates, CGST/SGST, payment details, and totals. Generation is idempotent by `(bill_id, bill_hash, template_version)`.

The analysis deck is generated from a materialized dataset containing sales by day, tender mix, GST by slab, top items by quantity and revenue, and current stock health. Charts use the real dataset. Any model-authored insight receives only that structured dataset and cannot invent numbers.

Local artifacts live under a non-source runtime directory. Production uses durable object storage and persists only metadata and storage keys in the database. Secrets and generated customer artifacts are never committed to the public repository.

## Seed catalog

The target seed is 120–150 realistic products across staples, pulses, oils, dairy, biscuits, snacks, noodles, beverages, spices, household cleaning, personal care, and 15–20 loose goods. Each row includes aliases, unit conversion, cost, sell price, MRP, HSN, GST slab, reorder level, opening stock, and the tax-data effective date.

Seed prices are labelled demonstration values. GST classifications must be reviewed against an authoritative source before the project claims production tax compliance; the model never fills missing classification data from memory.

## MVP assumptions and exclusions

The evaluator-facing behavior is based on these explicit decisions:

- One authorized store and owner; schema remains store-scoped.
- Intra-state B2C invoices only; no IGST.
- MRP and sell prices are tax-inclusive.
- Moving weighted-average cost controls the below-cost guard.
- Packaged products cannot be sold above MRP.
- One tender per bill; UPI and Card require a reference, Cash does not.
- A bill is committed only after a preview and explicit confirmation.
- No stock reservation while a draft is open; stock is rechecked at finalize.
- Khata accounts are opened explicitly, partial repayment is supported, and overpayment is rejected.
- “Close the day” is an as-of report in `Asia/Kolkata`; it does not lock the accounting date.
- No discounts, split tender, prepayments, returns, refunds, invoice voiding, stock deletion, supplier payables, expiry batches, FEFO, barcode/photo recognition, voice notes, multilingual parsing, or scheduled reminders in the MVP.
- Finalized corrections, if later added, use reversal records rather than deletion.

## Security and operations

- Default to a configured Telegram user-ID allowlist. An explicit `ALLOW_ALL_TELEGRAM_USERS=true` demo override permits every identified sender and must never be treated as read-only or production-safe.
- Keep OpenAI and Telegram credentials in environment variables; commit only an example environment file.
- Never log secrets. Minimize customer data in model traces and disable sensitive trace payloads by default.
- Validate document text and filenames before rendering or uploading.
- Use UTC timestamps internally and the store timezone for business dates.
- Record structured run IDs, tool calls, durations, token usage, domain errors, and delivery attempts for debugging.
