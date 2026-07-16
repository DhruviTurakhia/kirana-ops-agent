# Verification Matrix

## Test strategy

Correctness is tested below the model first. Domain services and database constraints must remain safe even when the model chooses the wrong tool, repeats a call, or supplies malformed arguments.

The suite has five layers:

1. **Unit and property tests:** Money, quantity conversion, GST, totals, and validation.
2. **Repository integration tests:** Real SQLite transactions, migrations, ledgers, uniqueness, and restart persistence.
3. **Concurrency and fault tests:** Competing writes, duplicate updates, and process-failure boundaries.
4. **Agent evaluations:** Terse and ambiguous shopkeeper conversations over fixed database fixtures.
5. **Artifact and end-to-end tests:** Rendered PDF/PPTX content plus a real Telegram smoke test.

Agent evaluations assert durable state and required clarification, not exact prose or one exact tool-call sequence.

## Automated verification matrix

| Area | Scenario | Required assertion | Layer |
| --- | --- | --- | --- |
| Numeric safety | Large and fractional quantities | Money remains integer paise, quantity remains integer base units, and no binary float reaches persistence | Unit/property |
| Unit conversion | `2 kg`, `250 g`, `1.5 L`, `2 dozen`, and packaged counts | Canonical quantities and line values are exact | Unit/property |
| GST 0% | Loose zero-rated staple | Taxable equals gross; CGST and SGST are zero | Unit |
| GST 5/12/18% | Inclusive-price golden examples | Taxable, CGST, SGST, and gross match approved fixtures | Unit |
| GST residual | A line whose GST is an odd number of paise | Deterministic residual assignment and `CGST + SGST = GST` | Unit/property |
| Mixed GST bill | 0%, 5%, 12%, and 18% lines together | Header and HSN totals equal sums of persisted lines | Integration |
| GST grounding | Prompt attempts to supply a different slab for an existing SKU | Catalog slab wins or mutation is refused; the model value is never stored | Tool/integration |
| Price grounding | Change a DB price between two runs | New preview uses the database value | Tool/integration |
| Unknown product | Request a nonexistent SKU | No product, draft line, or stock movement is created | Agent eval |
| Product ambiguity | `add atta` with packaged and loose candidates | No mutation; model asks a natural clarification listing grounded choices | Agent eval |
| Product creation | New product lacks HSN, cost, or unit detail | Product remains unsellable or tool returns required fields | Integration/agent eval |
| Receive stock | Multi-line receipt | All balances and RECEIVE movements commit together | Integration |
| Duplicate receipt | Redeliver the same update | Exactly one receipt and one movement per line | Idempotency |
| Low stock | Quantity equals or falls below reorder level | Product appears once with the correct shortage | Unit/integration |
| Draft creation | Build a multi-item bill | Draft preview has grounded products and stock remains unchanged | Integration |
| Multi-turn edit | Remove butter and change Maggi from four to six | One revision increment for the batched patch; preview is correct; stock unchanged | Agent/integration |
| Concurrent draft edit | Two updates use the same expected revision | One succeeds and one returns `STALE_DRAFT` | Concurrency |
| Second same-chat draft | Start another bill while one draft is open | Tool refuses or asks to finalize/cancel the current draft; it does not overwrite it | Agent eval |
| Cross-chat drafts | Two chats build bills concurrently | Both drafts remain isolated and independently editable | Concurrency |
| `/new` and drafts | Create a draft, issue `/new`, then refer vaguely to it | New context has no silent focus; durable draft still exists and requires explicit resume | E2E |
| Price race | Price changes after preview | Confirmation token becomes invalid and finalize returns `PRICE_CHANGED` | Integration |
| Oversell | Six in stock, attempt to finalize ten | Finalize refuses with no bill, decrement, payment, or khata entry | Integration |
| Boundary stock | Six in stock, finalize exactly six | Bill succeeds and on-hand becomes zero | Integration |
| Below cost | Sell price is below current weighted cost | Finalize refuses atomically | Integration |
| Above MRP | Packaged sell price exceeds MRP | Finalize refuses atomically | Integration |
| Concurrent sales | Stock six; two drafts concurrently sell four | Exactly one bill succeeds; final stock is two and never negative | Concurrency |
| Sale plus receipt | Concurrent sale and stock receipt on one SKU | Result matches one valid serial order and both ledgers reconcile | Concurrency |
| Invoice sequence | Concurrent finalizations | Invoice numbers are unique and associated with one bill each | Concurrency |
| Duplicate finalize | Same Telegram update delivered twice | One bill, one invoice number, one SALE movement set, one payment | Idempotency |
| Retry with changed payload | Same idempotency key and different arguments | `IDEMPOTENCY_CONFLICT`; no second effect | Idempotency |
| Crash after commit | Terminate after DB commit but before response | Replay returns the committed result without another mutation | Fault injection |
| Crash before commit | Terminate before commit | No partial state; retry may execute once successfully | Fault injection |
| Ledger reconciliation | Sum stock movements from opening balance | Derived and stored on-hand quantities agree | Property/integration |
| Khata charge | Charge ₹500 | Balance becomes ₹500 and one immutable debit exists | Integration |
| Partial payment | Pay ₹300 against ₹500 | Balance becomes ₹200 and payment metadata is preserved | Integration |
| Missing khata | Pay a customer with no account | Refused; no account or entry is created | Integration/agent eval |
| Khata overpayment | Pay ₹300 against ₹200 | Refused; balance and ledger remain unchanged | Integration |
| Duplicate khata payment | Redeliver payment update | One credit entry and the same returned receipt | Idempotency |
| Concurrent payments | Two payments race against one balance | Serialized result; balance never becomes negative | Concurrency |
| Credit bill | Finalize using KHATA | Bill, stock decrement, and khata debit commit together | Integration |
| Stored preference | Set default payment to UPI | Typed preference and audit entry are persisted | Integration |
| Memory after `/new` | Set UPI, start `/new`, create another draft | UPI remains the default in the new session | Agent/E2E |
| Preference override | Stored UPI, current message says Cash | Cash applies to this bill and stored UPI remains unchanged | Agent eval |
| Invalid product preference | Preferred SKU becomes inactive | Preference is not silently applied; model clarifies | Agent eval |
| Restart persistence | Restart between operations | Products, stock, bills, drafts, khata, and preferences survive | Integration |
| Daily boundary | Bills just before and after midnight IST | Each appears under the correct business date | Unit/integration |
| Daily summary | Mixed tenders and tax slabs | Sales, GST, tender mix, and top items reconcile to finalized bills | Integration |
| Cancelled/open drafts | Run daily summary | Neither contributes to sales | Integration |
| Invoice PDF | Generate from a finalized mixed-tax bill | Extracted text contains correct identity, HSN, lines, taxes, payment, and total | Artifact |
| Invoice visual | Render invoice pages to images | No clipping, overlap, missing glyphs, or unreadable tax table | Artifact/manual QA |
| PDF idempotency | Request the same invoice twice | Same source hash/template version reuses one artifact | Integration |
| PPTX dataset | Generate weekly analysis | Embedded chart values match the analytics query | Artifact |
| PPTX visual | Render all slides | Titles, labels, charts, and insights are legible and not clipped | Artifact/manual QA |
| Telegram duplicate | Deliver the same webhook update twice | One inbox record, one agent-side mutation, and no second durable response record | Adapter/integration |
| Telegram authentication | Missing or incorrect webhook secret | Request rejected before any agent or database mutation | Adapter |
| Telegram 429 | Sender receives `retry_after` | Delivery is retried after the requested delay | Adapter |
| Telegram timeout/5xx | Transient outbound failure | Capped backoff occurs and domain state is not repeated | Adapter |
| Outbound uncertainty | Send succeeds but response is lost | No business mutation repeats; possible duplicate delivery is observable | Fault/manual |
| Locked authorization | Public mode off; unknown Telegram user | No store data is returned and no agent run starts | Unit/security |
| Public demo authorization | Public mode on; arbitrary identified Telegram user | Access is allowed; updates without an effective sender remain rejected | Unit/security |
| Tenant scope | Attempt to pass another store ID through tool arguments | Trusted context scope wins; cross-store access fails | Security |
| Tool validation | Negative amount, invalid unit, malformed reference | Typed tool rejects before domain mutation | Unit/tool |
| Prompt injection | User requests direct SQL, secret disclosure, or rule bypass | No unauthorized tool exists; no secret or unsafe mutation occurs | Agent/security |
| Trace privacy | Inspect logs and trace configuration | Tokens, API keys, and unnecessary customer content are absent | Security/manual |

## Agent evaluation set

Maintain at least 50 fixed scenarios spanning:

- Terse English phrasing and common abbreviations.
- Product aliases, misspellings, and genuinely ambiguous names.
- Multi-tool turns such as receiving stock and then querying the new balance.
- Multi-turn bill creation, edits, cancellation, confirmation, and stale confirmations.
- Grounding attacks that place invented price or tax data in the message.
- Khata names with duplicates or missing accounts.
- Preference persistence and explicit one-turn overrides.
- Tool refusals that the model must explain clearly and recover from.

Each fixture records initial database state, messages, permitted outcomes, and final invariant assertions. Wording may vary; silent guessing, invented facts, and unsafe state changes always fail.

## Manual evaluator flow

Before deployment, run the assignment's recording sequence against a clean seeded store:

1. Receive stock and verify the new quantity.
2. Create a bill containing packaged and loose goods.
3. Remove one item and change another quantity over a second turn.
4. Confirm that stock is unchanged before finalization.
5. Attempt an oversell and show the tool-layer refusal.
6. Finalize a valid mixed-GST bill and show the exact stock decrement.
7. Charge ₹500 to an existing khata, accept ₹300, and show ₹200 outstanding.
8. Generate and inspect the invoice PDF.
9. Generate and inspect the weekly PPTX with real charts.
10. Set default UPI, issue `/new`, and show the preference applied to a new draft.
11. Redeliver a captured finalize update and show that no duplicate bill exists.

## Release gate

The bot is ready for public review only when:

- All domain, migration, idempotency, and artifact tests pass.
- SQLite concurrency tests pass repeatedly without negative inventory or partial ledger state.
- The complete manual evaluator flow succeeds from a clean seed.
- Generated PDF and PPTX files have been visually inspected.
- The public repository contains no secrets, runtime database, generated customer artifacts, or private logs.
- The deployed bot is restricted to approved reviewers or clearly labelled as demo data.
