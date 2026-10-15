# MORTGPU demo video: voiceover script (draft v1)

Target length is 1:55 or less, about 275 words at about 150 wpm. The narration is written for TTS (breeze-tts): amounts are spelled out, and "MORT G P U", "ross-ka" and "cog-nee" are spelled as they should be spoken.
All figures come from the real run in `/tmp/demo.log` (Stripe test mode).

| # | ~Time | Narration (TTS text) | On screen |
|---|---|---|---|
| 0 | 0:00–0:10 | A serious GPU costs ten thousand pounds. Most builders will never have that in one go. | £10,000 price tag on a GPU; it slides out of reach |
| 1 | 0:10–0:24 | MORT G P U is a ross-ka for GPUs, a rotating savings club. Members save into a pool and earn credits for patience. The top of the queue gets the next GPU, and their savings become the deposit. | Logo sting. Queue animates: Amara rises to #1 with 800,000 credits |
| 2 | 0:24–0:38 | No human runs it. An AI agent proposes every action. A deterministic policy engine allows or blocks it, citing a clause. Only then does it touch Stripe. And every step is remembered in cog-nee and written to an audit ledger. | Pipeline: PROPOSE → GATE (ALLOW/BLOCK §) → STRIPE → COGNEE → LEDGER; "no human in the loop" |
| 3 | 0:38–0:54 | Amara's contract arrives. Her two thousand pounds of savings become twenty percent equity. The gate checks five clauses, the supplier is paid, and GPU zero zero one comes online. Stripe bills her rent: four hundred pounds a month, shared out to the other members. | Contract → ALLOW D-a863f5 §2.1–2.5 → GPU-001 → rent sub qty 800 × £0.50 = £400 → split Priya £213.90 · Tom £149.73 · Ben £25.67 · Carl £10.70 |
| 4 | 0:54–1:06 | She resells inference. Nova Labs runs thirty thousand GPU minutes. Each one is a Stripe meter event, invoiced at twelve hundred pounds. Her income covers her rent three times over. | Meter ticks to 30,000 → invoice £1,200 paid → ICR 300% ≥ 125% ✓ |
| 5 | 1:06–1:14 | She emails to buy back a thousand pounds. Approved, charged, and her rent drops to three hundred and fifty. | Email → ALLOW §5.2 → £400 → £350 |
| 6 | 1:14–1:36 | Carl's rent fails. He asks to skip a month. The agent recalls from memory that he already took a holiday this year, and broke a promise to pay double. Blocked, under clause four point two. His last four percent equity covers the arrears, eighty pounds is written off, and his GPU key is revoked. | Invoice FAILED £480 → email "can I skip?" → Cognee recall card → **BLOCK D-67c0b5 §4.2** → equity 4% → 0% → £80 written off → old key → **HTTP 403** |
| 7 | 1:36–1:44 | Ben pays his final five hundred pounds. He now owns one hundred percent, and the title is his. Even the pool can't revoke it. | £500 → 100% → title GPU-002 → Ben; pool revoke → **HTTP 409** |
| 8 | 1:44–1:55 | Ask why Carl was disconnected, and it answers from memory, clause by clause. Every pound explained. The books balance. MORT G P U: save together, own your compute. | Q&A card citing §4.1–§4.5 → savings £19,100 = cash £2,100 + stakes £17,000 ✓ → logo + "Stripe · Cognee · Claude" |

Word count is about 280. If the TTS runs long, trim beat 5 first, then the second sentence of beat 2.
