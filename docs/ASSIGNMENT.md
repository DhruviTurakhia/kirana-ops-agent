# Supermarket Ops Agent

BigMantra Engineering — Take-Home Assignment

**Interface:** Telegram only
**Harness:** Candidate's choice of modern agent SDK

## 1. The brief

Build a conversational agent that runs a small Indian supermarket or kirana store end to end. The owner operates the whole shop from Telegram in plain language: receiving stock, cutting bills, checking inventory, running customer credit, closing the day, and requesting invoices and analysis decks.

There is no web app, admin panel, or form. The chat is the product.

This is not a CRUD application with a chatbot placed in front of it. The agent must reason over messy human requests while keeping the store's books consistent. The candidate authors the skills and tools that operate the store and lets the model orchestrate them. A large regex or `if`/`elif` intent router is the wrong design.

Use any modern agent harness, such as:

- Claude Agent SDK
- deep agent
- Vercel AI SDK
- An equivalent modern agent harness

Designing the skill and tool surface, and justifying the chosen harness, is a core part of the exercise.

## 2. Domain: Indian kirana or supermarket

Model a realistic Indian grocery store.

- **Currency:** ₹ (INR).
- **Products:** Real SKUs such as Aashirvaad Atta 5 kg, Tata Salt 1 kg, Amul Butter 100 g, Fortune Sunflower Oil 1 L, Maggi 70 g, Parle-G, and Surf Excel, plus loose products such as sugar, rice, and dal sold by weight.
- **Units:** kg, g, litre, ml, packet, dozen, and piece. Distinguish loose and packaged products.
- **GST:** Compute tax correctly. Many staples are 0%, packaged staples are commonly 5%, and FMCG products such as chocolates and soaps may be 12–18%. For intra-state sales, split GST into CGST and SGST. Every item carries an HSN code and tax slab. Bills show a legible tax breakup and round correctly.
- **Payments:** Record Cash, UPI, or Card together with a reference. No real payment gateway is needed.
- **Khata:** Customer credit is a first-class concept. Examples include “Put ₹500 on Ramesh's credit,” “What's Ramesh's balance?”, and “Ramesh paid ₹300.”
- **Stock discipline:** Every SKU has a cost price, MRP or sell price, quantity, and reorder level. Selling decrements stock atomically.
- **Language:** The owner writes terse, plain English in the style of a real shopkeeper.

## 3. Required capabilities

These are capabilities, not fixed commands. The agent decides which tools to call.

| Capability | Example message |
| --- | --- |
| Receive stock | `50 packets of Maggi came in, cost ₹12, MRP ₹14` |
| Add a new product | `new item: Amul Butter 100g, GST 12%, MRP ₹62` |
| Cut a bill | `make a bill: 2kg sugar, 1 Aashirvaad atta 5kg, 4 Maggi, 1 Amul butter, UPI` |
| Edit a bill mid-build | `drop the butter, make it 6 Maggi` |
| Query stock | `how much sugar is left?` |
| Find low stock | `what's running out?` |
| Use khata | `put ₹500 on Ramesh's credit` · `Ramesh paid ₹300` · `Ramesh's balance?` |
| Close the day | `today's sales?` or `close the day` → total, tax collected, payment mix, and top items |
| Generate invoice PDF | `send me that bill as a PDF` → clean, GST-correct invoice |
| Generate analysis deck | `make this week's sales analysis deck` → PPTX with charts and insights |
| Set a preference | `always assume UPI unless I say cash` · `default atta = Aashirvaad 5kg` |

Preferences must be remembered across chats.

When a request is genuinely ambiguous, the agent asks a clarifying question rather than guessing. For example, “add atta” may require asking whether the owner means Aashirvaad 5 kg or loose atta. The model must formulate that clarification; it must not come from a hardcoded intent branch.

## 4. Hard parts

The following requirements distinguish a robust agent from a toy implementation. The README must explain how each is handled.

- **Grounding:** Prices, GST slabs, and stock come from the database through tools. The model never invents a product or price.
- **Oversell guard:** Stock cannot become negative. Billing ten units when only six exist is rejected at the tool layer, not merely discouraged by the prompt.
- **GST correctness:** Support per-item slabs, CGST/SGST splitting, deterministic rounding, and a readable bill-level tax breakup.
- **Multi-turn bills:** A bill is built over several messages, supports edits, and changes stock only when finalized.
- **Idempotency:** Telegram may redeliver updates. A retried finalize operation must not create a second bill or decrement stock twice.
- **Concurrency:** Two bills, or a sale and stock receipt, may be in flight together without corrupting inventory.
- **Guardrails:** Do not sell below cost, delete stock, or settle a khata that does not exist. Confirm or refuse unsafe operations.
- **Real artifacts:** Generate a proper GST invoice PDF and a PowerPoint analysis deck with real charts. Screenshots and plain-text substitutes do not qualify.
- **Memory across sessions:** Standing preferences such as payment mode, preferred brand, shop name, and GSTIN survive `/new` and application restarts. Memory lives outside the model context window.

## 5. Architecture requirements

- Design the skills and tools. The assignment deliberately does not prescribe a tool list.
- Use the conventions of the chosen harness: skill files, tool schemas, agents, or subagents as appropriate.
- Keep the design agent-first. Natural language goes through the model and its tools; a regex or keyword router must not perform the real work.
- Put business rules where data changes. Oversell prevention, GST arithmetic, idempotency, and khata rules belong in domain services and tools, not only in the system prompt.
- Implement a real control loop: observe → reason → act through tools → feed the result back → continue. A single turn may chain several tool calls.
- Persist stock, bills, khata, owner preferences, and conversation state in SQLite or PostgreSQL so they survive restart.
- `/new` clears conversation context but not store knowledge or standing preferences.
- Do not model the product as a LangGraph-style node-per-command state machine.

## 6. Deliverables

- A deployed Telegram bot that remains available during review, with its `@username` in the README.
- A modern agent harness.
- The authored skills and tools used to run the store.
- A clean GST invoice PDF for any finalized bill.
- A PowerPoint analysis deck covering sales, top items, stock health, and GST collected, with real charts.
- A roughly one-page README explaining the harness choice, control loop, skill/tool design, and the solution to each hard part.
- A four-to-five-minute recording showing:
  1. Receiving stock.
  2. Building and editing a multi-item bill.
  3. The oversell guard.
  4. A complete khata cycle.
  5. Invoice PDF generation.
  6. Analysis deck generation.
  7. Setting a preference, starting `/new`, and showing that it remains remembered.
- Questions to the evaluators where product requirements are genuinely ambiguous.

## 7. Optional stretch goals

- Branded or templated invoice PDFs.
- A scheduled weekly analysis deck sent automatically.
- Reorder suggestions based on sales velocity.
- Expiry and batch tracking with FEFO.
- Voice-note orders that are transcribed before processing.
- Hindi or Tamil support.
- Barcode or product-photo identification.
- Khata payment reminders.
