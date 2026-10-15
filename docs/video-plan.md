# MORTGPU demo video: plan

**Deliverable:** a 1920×1080, 30fps MP4 that runs **1:58**, rendered with HyperFrames and narrated with the Drama 3 voiceover (v2).
**Message:** an agent runs a GPU savings club, and it bills, enforces and explains every pound with no human in the loop.
**Audience:** Stripe hackathon judges, scoring Works end to end 35 · Autonomy with control 25 · Explains every pound 20 · Product 20.
**Approach:** all-HyperFrames first. Every beat has a marked **footage slot** where real screen capture can replace the animated mock later.
**Deadline:** submit by 21:00 on 15 Oct 2026. The render has to be finished by **20:45**.

## Inputs

| Asset | Path |
|---|---|
| Voiceover beats (v2, edge silence trimmed) | `media/vo/vo_00.mp3` … `vo_08.mp3`, text in `media/vo/beats.json` |
| Full voiceover preview | `media/vo/voiceover_v2.mp3` (1:55) |
| Logo | `docs/brand/logo.svg` (MORT in red `#ff4136`, GPU in green `#34d973`, chip mark) |
| Style | `docs/STYLE.md` + `docs/theme.css` |
| Figures | the real run (`/tmp/demo.log`, Stripe test mode); the numbers below are copied from it |

## Design system (from STYLE.md, no exceptions)

- **Background:** always dark, `--grad-page`: a red glow top-right and a green glow bottom-left over the `#1c0d0c → #161616 → #0c1710` diagonal. No light frames, ever.
- **Corners:** hard corners everywhere (`border-radius: 0`). Cards use `--grad-surface` with a 1px `#2e2e2e` border and a 4px left accent bar in red or green.
- **Colour:** red and green are the only accents. Red means attention, BLOCK, failure and money owed. Green means ALLOW, paid and ownership.
- **Type:** Saira (800/900 for headlines, 400/500 for body); JetBrains Mono for IDs, clauses, HTTP codes and amounts in "terminal" cards.
- **Recurring elements:**
  - **Gate chip:** `ALLOW D-xxxxxx §2.1` in green, or `BLOCK D-xxxxxx §4.2` in red, stamped in with a short overshoot.
  - **Stripe card:** an invoice, subscription or meter mock in the dark theme, carrying a small "Stripe test mode" tag and the `decision_id` / `policy_clause` metadata rows.
  - **Pipeline bar:** PROPOSE → GATE → STRIPE → COGNEE → LEDGER. It's introduced in beat 2 and then sits as a slim strip along the bottom of beats 3–7. The active stage lights up on each action, which ties the story back to the control loop.
  - **Caption line:** the key phrase of each beat, bottom-left in Saira 500, for judges watching muted. Captions are kept short and are not a full transcript.
- **Motion:** GSAP, quick and confident (0.3–0.5s ease-out entrances, staggered lists, count-ups for money). Cuts between beats are hard or a fast 0.25s slide. No bouncy or playful easing except on the gate stamp.

## Timeline (seconds, locked to the voiceover)

Each beat's voiceover starts at the time shown, with 0.45s gaps. The total runs to 115.9s, plus a 2s logo hold, giving **117.9s**.

| # | Start | VO end | Beat | Visuals | Key figures | Footage slot (later) |
|---|---|---|---|---|---|---|
| 0 | 0.50 | 6.95 | **Hook** | A GPU silhouette with a £10,000 tag; the tag counts up from £0, then a red "out of reach" line strikes through it | £10,000 | none |
| 1 | 7.40 | 21.74 | **What it is** | Logo sting (MORT drops in red, GPU in green, rim line wipes left to right). Members' savings stack into a "pool" bar, credits = savings × days, and the queue table sorts with Amara rising to #1 | Amara 800,000 credits · Tom 630,000 · Priya 600,000 | live view queue panel |
| 2 | 22.19 | 36.59 | **Control loop** | The pipeline is built stage by stage, in step with the narration. "NO HUMAN IN THE LOOP" banner. The GATE stage shows ALLOW in green and BLOCK in red side by side with "§ clause" | policy.json · Stripe · Cognee · ledger | terminal showing PROPOSE/GATE lines |
| 3 | 37.04 | 53.86 | **Contract → bill** | A contract card slides in; £2,000 → 20% equity ring; five gate checks tick (§2.1–2.5) → `ALLOW D-a863f5`; supplier paid £10,000; GPU-001 tile goes online in green; Stripe subscription card `qty 800 × £0.50 = £400.00/mo`; rent splits to four members | Priya £213.90 · Tom £149.73 · Ben £25.67 · Carl £10.70 | Stripe dashboard: Amara's subscription + metadata |
| 4 | 54.31 | 63.32 | **Usage → bill** | Nova Labs → GPU-001; meter counter rolls 0 → 30,000 GPU-min with event ticks; invoice card £1,200.00 PAID; ICR gauge fills to 300% (125% threshold marked) | 30,000 min × £0.04 = £1,200 · ICR 300% | Stripe meter + Nova invoice |
| 5 | 63.77 | 68.34 | **Buy-back** | Email bubble "buy back £1,000" → `ALLOW D-963715 §5.2` → equity 20%→30% → rent £400 strikes to £350 | £1,000 · £400 → £350 | Stripe PaymentIntent |
| 6 | 68.79 | 89.20 | **Enforcement** | The hero beat; see below | £480 failed · 4% = £400 · £80 written off · HTTP 403 | terminal BLOCK panel + live view |
| 7 | 89.65 | 98.66 | **Payoff** | £500 → ownership bar fills to 100% green; title card GPU-002 → Ben; pool's revoke attempt bounces with **HTTP 409** | £500 · 100% · 409 | terminal 409 line |
| 8 | 99.11 | 115.46 | **Explain + close** | Question "Why was Carl cut off?" types in; answer card cites §4.1 · §4.2 · §4.3 · §4.4 · §4.5; books equation assembles **£19,100 = £2,100 + £17,000 ✓**; cut to logo + "save together, own your compute" + "Stripe test mode · Cognee · Claude" | £19,100 = £2,100 + £17,000 | Stripe dashboard overview |
| end | 115.9 | — | Logo hold 2s | | | |

### Beat 6 in detail (the money shot, 20.4s)

1. Carl's invoice card flips to **FAILED £480.00** (red) on "Carl's rent fails".
2. Email bubble: "can I skip a month?"
3. **Cognee recall panel** (green accent, labelled "memory"): two items fade in, "1 payment holiday in last 12 months" and "Aug 2026: promised to pay double. **Broken**."
4. On "Blocked": the red **`BLOCK D-67c0b5 §4.2`** stamp. The screen gets a subtle red edge glow, and the pipeline's GATE stage turns red.
5. Equity bar 4% → 0% ("£400 at locked price"), then "£80 written off · savings untouched".
6. GPU-003 tile: key icon → **HTTP 403**, and the tile dims to "relisted, pool-owned".

## Build structure

- HyperFrames project in `video/` (created with `hyperframes init`, so it starts as an empty dir), with one root `index.html`. Each beat is a sub-composition (`beats/b0-hook.html` … `b8-close.html`) placed at the start times above. There is a separate audio track with the 9 VO clips, and a persistent pipeline-strip track for beats 3–7.
- Shared `theme.css` copied from `docs/theme.css`, and `logo.svg` copied in.
- **Footage splice later:** each beat's main visual sits in a `.slot` wrapper. Replacing it with a `<video>` clip of the same duration (captures from the live view, terminal or Stripe dashboard) changes nothing else.

## Order of work (time-boxed)

| Until | Step |
|---|---|
| 20:15 | `hyperframes init video`, BRIEF.md, theme, audio track, empty beat shells at the right times; `hyperframes check` |
| 20:30 | Build beats 0, 1, 2, 6, 8 (hook, concept, loop, money shot, close), which are enough to submit if time runs out |
| 20:38 | Beats 3, 4, 5, 7 |
| 20:42 | Lint/check, snapshot a few frames, fix anything off-brand |
| 20:45 | Render MP4 → you upload (unlisted YouTube/Loom) → submit by 21:00 |

**Fallback:** if a beat isn't ready by 20:38, it ships as a clean title card (caption + key figure on the dark gradient) so the render never slips.
