# Evaluator Recording Guide (4–5 Minutes)

Use this guide to record one short, easy-to-follow demonstration of the Kirana Ops Agent. The target runtime is **4 minutes 50 seconds**.

The recording should prove that the public Telegram bot and the public GitHub repository are real, connected, and working. Use only fictional demo data.

> **Never show a secret.** Do not open `.env`, BotFather, an OpenAI dashboard, deployment environment variables, API keys, bot tokens, terminal history containing secrets, or real customer information.

## 1. Pre-recording checklist

Complete every item before pressing Record.

- [ ] Confirm the public repository opens at <https://github.com/DhruviTurakhia/kirana-ops-agent> without signing in.
- [ ] Confirm [@dhruvi_ai_shop_bot](https://t.me/dhruvi_ai_shop_bot) is deployed and replies to the authorized demo account.
- [ ] Update the README bot status only after the bot is genuinely live. Do not claim that a pending deployment is live.
- [ ] Use a fresh disposable demo database, run `uv run kirana-seed`, and start the bot with `uv run kirana-bot` before recording.
- [ ] Keep the configuration file closed. The recording does not need to show how credentials were entered.
- [ ] Confirm there is no open bill draft, no customer named `Ramesh Demo`, and no saved default payment preference for the recording account.
- [ ] Confirm the opening stock below. If any number differs, reset and reseed the disposable demo database before recording.

| SKU | Seeded product | Required opening stock |
|---|---|---:|
| `NB-001` | Maggi 2-Minute Noodles Masala 70g | 6 packets |
| `DR-001` | Amul Butter 100g | 3 packets |
| `PS-001` | Aashirvaad Shudh Chakki Atta 5kg | 25 packets |
| `LS-009` | Loose Toor Dal | 35 kg |
| `BS-001` | Parle-G Original Biscuits 79g | 60 packets |

- [ ] Send one harmless test message before recording to confirm the bot is responsive. Then reset the disposable database again so the timeline starts clean.
- [ ] Confirm PDF and PowerPoint files can be uploaded to Telegram from the deployed environment.
- [ ] Run the tests before recording. The exact count may change; the important proof is that the full test run passes.
- [ ] Open only two windows or tabs: the Telegram chat and the public GitHub repository.
- [ ] Close notifications, private chats, password managers, cloud consoles, and unrelated browser tabs.
- [ ] Set screen zoom large enough that Telegram messages, filenames, the GitHub URL, the Public badge, and the test status are readable.
- [ ] Record at 1080p if available. Keep the pointer still while the evaluator reads a result.
- [ ] Copy the messages in this guide into a plain scratchpad so they can be pasted quickly. The scratchpad must contain no secrets.

## 2. Expected numbers for the recording

These numbers make it easy to spot a dirty or incorrectly seeded database before wasting a take.

- Receiving 20 Amul Butter packets changes its stock from **3 to 23**.
- The first four-item draft totals **₹671.50**.
- Removing butter and changing Maggi from four to six produces a three-item draft totaling **₹641.50**.
- The final valid bill includes **₹23.76 GST**: ₹14.76 on atta and ₹9.00 on Maggi; loose toor dal is shown at 0% GST in the demo data.
- Finalizing that bill consumes all six seeded Maggi packets, so Maggi stock becomes **0**.
- Trying to sell ten more Maggi packets must be refused with **10 requested and 0 available**.
- Charging ₹500 and recording a ₹300 payment leaves the demo Khata balance at **₹200**.
- After saving default UPI, `/new` clears conversation context but preserves the preference. A new ₹10 Parle-G draft should therefore show **UPI**.

The exact prose returned by the model can vary. The stored values, safety decision, attachment type, and persisted preference are the proof.

## 3. Exact 4-minute-50-second recording timeline

### 0:00–0:20 — Introduce the real bot

1. Start on the Telegram chat with the header visible.
2. Point briefly to `@dhruvi_ai_shop_bot`.
3. Say:

   > This is the live Kirana Ops Agent. I will use ordinary shopkeeper language; the OpenAI agent chooses typed inventory, billing, Khata, reporting, and memory tools behind the conversation.

Do not show BotFather, tokens, keys, or deployment settings.

### 0:20–0:48 — Receive stock

Send this exact message:

```text
20 packets of Amul Butter 100g came in. Cost ₹48 each, MRP ₹58, sell at ₹58, reference GRN-DEMO-001.
```

Expected visible result:

- The bot identifies Amul Butter 100g.
- It confirms 20 packets received with the supplied cost, MRP, selling price, and reference.
- Stock changes from 3 to **23 packets**.

Say:

> This is a real inventory mutation, not a chat-only answer.

### 0:48–1:18 — Start a multi-item bill draft

Send:

```text
Create a cash bill with 1 Aashirvaad Shudh Chakki Atta 5kg, 1.5 kg Loose Toor Dal, 4 Maggi 2-Minute Noodles Masala 70g, and 1 Amul Butter 100g. Show me the draft; do not finalize.
```

Expected visible result:

- An open **Cash** draft with four distinct products.
- Quantities are 1 packet, 1.5 kg, 4 packets, and 1 packet.
- The preview total is **₹671.50**.
- The preview shows a GST breakdown with different product tax rates.
- Stock has not changed because a preview is non-mutating.

Say:

> The agent grounded each spoken product to a seeded SKU and fetched price, HSN, GST, and stock from the tools.

### 1:18–1:40 — Demonstrate a multi-turn edit

Send:

```text
Remove the butter, change Maggi to 6 packets, and show the updated draft.
```

Expected visible result:

- It edits the same draft instead of creating a second bill.
- Butter disappears.
- Maggi changes from 4 to **6 packets**.
- The revised total is **₹641.50**, including **₹23.76 GST**.
- The bill remains a draft and stock is still unchanged.

### 1:40–2:00 — Finalize the valid bill

Send:

```text
Finalize this bill.
```

Expected visible result:

- The bot confirms finalization and shows an invoice number similar to `AKD/26-27/000001`.
- The total remains **₹641.50**.
- Maggi stock becomes **0**, atta becomes 24 packets, and loose toor dal becomes 33.5 kg.
- Amul Butter remains at 23 packets because it was removed before finalization.

Keep the invoice number visible or copy it to the harmless scratchpad; it may be useful if the PDF request needs clarification.

Say:

> Finalization rechecks the draft and stock inside one transaction, then stores an immutable invoice and decrements inventory.

### 2:00–2:25 — Demonstrate the oversell guard

Send:

```text
Start a cash bill for 10 Maggi 2-Minute Noodles Masala 70g.
```

Expected visible result:

- The tool refuses the preview or sale because **10 packets were requested and 0 are available**.
- No second invoice is created and inventory never becomes negative.

If the reply says a failed draft remains open, send:

```text
Cancel this draft.
```

If it says no draft was created, skip the cancellation and continue. Say:

> The model cannot talk its way around this rule; the transactional tool enforces the stock check.

### 2:25–3:03 — Complete a Khata charge and payment cycle

Send these three messages in order:

```text
Create a customer named Ramesh Demo and charge ₹500 to his khata for groceries.
```

```text
Ramesh Demo paid ₹300 in cash.
```

```text
What is Ramesh Demo's current khata balance?
```

Expected visible result:

- The fictional customer is created and charged **₹500**.
- The Cash payment records **₹300**.
- The final outstanding balance is **₹200**.

If the first message triggers a legitimate confirmation question, reply:

```text
Yes. Create Ramesh Demo, then charge ₹500 with the note groceries.
```

Do not substitute a real customer's name or data.

### 3:03–3:23 — Generate and upload the invoice PDF

Send:

```text
Send the most recent finalized bill as a PDF.
```

Expected visible result:

- Telegram receives a `.pdf` document attachment.
- The filename or caption identifies the finalized invoice.
- The PDF contains the store header, line items, taxable value, CGST/SGST, GST total, and gross total.
- With the demo configuration it is visibly marked `DEMO · NOT A TAX INVOICE`.

If the agent asks which bill, reply with the invoice number shown at 1:40–2:00.

### 3:23–3:48 — Generate and upload the PowerPoint deck

Send:

```text
Create and send a PowerPoint sales analysis deck for this week.
```

Expected visible result:

- Telegram receives a `.pptx` document attachment.
- The response states the requested date range.
- The editable deck contains six slides with sales, GST, payment mix, product, inventory, and action-oriented analysis based on stored data.
- The charts use the real demo transactions rather than invented totals.

Say:

> Both artifacts are generated from the same stored operational data used by the agent.

### 3:48–4:23 — Prove durable preference memory across `/new`

Send:

```text
Always use UPI as my default payment mode unless I say otherwise.
```

Expected result: the bot confirms that the standing default is saved.

Now send the Telegram command:

```text
/new
```

Expected result: Telegram confirms that conversation context is clear while stock, bills, Khata, drafts, and standing preferences remain saved.

Finally send:

```text
Start a bill for 1 Parle-G Original Biscuits 79g. Show the draft only.
```

Expected visible result:

- A new draft totals **₹10.00**.
- Its payment mode is automatically **UPI**, even though `/new` rotated the conversation session.
- Do not finalize this UPI draft; finalization correctly requires a payment reference.

Say:

> `/new` clears conversational context, while deliberate operational preferences live in durable store memory.

### 4:23–4:50 — Show final repository and bot proof

1. Switch to <https://github.com/DhruviTurakhia/kirana-ops-agent>.
2. Keep the browser address bar and repository **Public** badge visible.
3. Show the README lines containing the same public repository URL and `@dhruvi_ai_shop_bot`.
4. Briefly show the latest passing GitHub Actions run, or the already-open passing local test result if Actions is unavailable.
5. Show that `.env.example` is present for configuration and that no `.env`, database, real customer data, or generated customer artifact is committed.
6. Switch back to Telegram for the final second, with the bot username and UPI draft visible together.

Say:

> The full implementation, tests, architecture, demo data, and setup instructions are public here, and this is the same bot responding live in Telegram.

Stop the recording at approximately **4:50**.

## 4. What the evaluator should have seen

Before submitting, watch the recording once and tick every item.

- [ ] A live Telegram bot with its public username visible.
- [ ] A successful stock receipt.
- [ ] A four-item draft created from natural language.
- [ ] A later message editing that same draft.
- [ ] Explicit finalization of the valid bill.
- [ ] A visible oversell refusal enforced by available stock.
- [ ] A ₹500 Khata charge, ₹300 payment, and ₹200 balance.
- [ ] A real invoice PDF attachment.
- [ ] A real PowerPoint attachment with six slides.
- [ ] A UPI preference saved, `/new` used, and UPI automatically reused afterward.
- [ ] The public GitHub URL, Public badge, repository evidence, and passing tests.
- [ ] No key, token, credential, private customer data, or private console was visible.

## 5. Fast troubleshooting before another take

- **A product is ambiguous:** resend the full seeded name and SKU, for example, `Use NB-001, Maggi 2-Minute Noodles Masala 70g.`
- **The first draft is not ₹671.50:** the database or prices are not at the expected clean seed state. Stop, reset the disposable demo database, and begin again.
- **The edited bill is not ₹641.50:** confirm butter was removed, Maggi is six packets, atta is one packet, and toor dal is 1.5 kg.
- **The oversell message shows stock above zero:** the valid bill did not finalize or the database was not clean. Do not improvise; reset and retry.
- **A stale draft blocks the Khata step:** cancel the draft in Telegram, then repeat the Khata message.
- **The PDF request selects the wrong bill:** resend the request with the exact invoice number displayed after finalization.
- **An artifact takes longer than expected:** keep recording or trim only silent waiting time. Do not cut out a result, refusal, or state-changing message.
- **`Ramesh Demo` already exists:** use a fresh disposable database so the expected ₹200 balance remains simple and reproducible.
- **UPI is not selected after `/new`:** verify that the bot confirmed the preference was saved before sending `/new`; then start a genuinely new draft.

After the recording, cancel the final Parle-G draft if desired and discard the disposable demo database. Neither action needs to appear in the video.
