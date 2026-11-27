# GT Voice Agent

A working prototype of an AI telecaller for Marico's general trade channel. It calls a retailer, confirms who it is talking to, brings up what is due for reorder, takes the order by voice in the retailer's language, reads it back, and only books it after a clear yes. The confirmed order goes out as a structured record a distributor's order system can take. Callbacks, complaints, escalations, do-not-call requests and market feedback are captured as records too.

The main design decision: **the language model handles the conversation, and plain code makes every decision that matters.** Stock checks, unit conversion, quantity sanity checks, the read-back rule, confirmation, identity and do-not-call are all enforced in Python. The model can only act through tools, and when it gets something wrong the engine refuses with a reason the model then follows. This is why the surprise scenarios hold up: they don't depend on the prompt being obeyed.

Slides: [solution presentation](docs/solution-presentation.pdf) (architecture, stack, integrations, business logic) and [scalability approach](docs/scalability-approach.pdf) (pilot to 5 lakh retailers a day). The PowerPoint versions are in `docs/` as well.

## Running it

You need Python 3.10 or newer and Chrome or Edge.

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
notepad .env
python main.py
```

macOS / Linux:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # then add your keys
python main.py
```

Open http://127.0.0.1:8000.

Keys go in `.env`:

| Key | What for | Where to get it |
|---|---|---|
| `GEMINI_API_KEY` | the agent's language model (required) | aistudio.google.com/apikey, free tier is enough for demos |
| `SARVAM_API_KEY` | speech-to-text and text-to-speech in Indian languages (recommended) | dashboard.sarvam.ai, new accounts get free credit |

Without a Sarvam key the app falls back to the browser's built-in speech (Chrome/Edge). It works, but Sarvam handles code-mixed Hindi/English and regional accents far better. Any OpenAI-compatible endpoint can replace Gemini; see `.env.example`.

## Using the demo

1. Pick a retailer and press **Call**. The middle and right panels show the live order, the engine log and the call brief.
2. Talk. Hands-free mode detects when you start and stop speaking, and you can interrupt the agent while it talks. Push-to-talk (button or space bar) is better in a noisy room.
3. You can also type what the retailer says. This is handy for testing exact phrases like "10 boxes" or "a few more than last time".
4. **Today's plan** shows who the agent would call today, in what order and why. **Outcomes** shows the order records, tasks, insights and call log. **Data notes** lists what the checks found in the workbook.

Retailers that show the data well:

| Retailer | Why |
|---|---|
| R003 Friends Provisions, Delhi (Hindi) | Saffola Gold 1 litre has only 13 in stock; Livon cross-sell |
| R043 Sai Kirana Store, Delhi (Hindi) | both Saffola Gold packs are out of stock at its distributor |
| R064 Mahalaxmi Agency, Indore (Hindi) | buys Parachute, Saffola Gold replenishment as cross-sell |
| R011 Ganesh Traders, Coimbatore (Tamil) | regular 200 ml order of 29, only 10 in stock |
| R008 Patel General Store, Pune (Marathi) | Livon Serum 100 ml out of stock, 50 ml offered instead |
| R001 Sharma Traders, Jaipur | the shop from the sample call. It is on the do-not-call list, so the call is refused |

## How a call works

```
 browser softphone ──audio──▶ Sarvam STT (saaras) ──text──▶ agent (Gemini, tool calling)
        ▲                                                      │ tools only
        └──audio── Sarvam TTS (bulbul) ◀──reply text──         ▼
                                              order engine: identity, catalogue, units,
                                              stock holds, order state, read-back, outcomes
                                                   │                 │
                                       distributor order record   tasks / insights / opt-outs
```

Each retailer turn is transcribed (with the detected language), passed to the agent, and the agent's reply is spoken in whatever language it replied in. The agent has 15 tools (`app/tools.py`). It cannot change prices, cannot add an item without the retailer's own words, and cannot place an order that hasn't been read back since the last change.

## Business logic

**When to call.** Every retailer gets a brief (`app/planner.py`). A shop is called only if it isn't on the do-not-call list (the data flag or an opt-out recorded on a call) and has at least one in-stock item that is due. The queue is ordered by the retailer's preferred calling window, then by value at stake: the suggested value of the lead items, weighted up when items are overdue. On the sample data that is 97 calls; R001, R025 and R082 are skipped.

**What to recommend.** An item is *overdue* once days since the last order pass the retailer's purchase frequency, and *due soon* at 75% of it. The agent leads with overdue items first, then highest value, and stops adding lead items once they cover the retailer's typical order value (at most four). Out-of-stock items are never offered; if the retailer asks for one, the engine returns an in-stock alternative from the same family, brand or category.

**How much.** It anchors on what the retailer actually ordered. If the last order moved more than 20% from the one before, it follows the last order; otherwise it suggests the average of the two. The suggestion is capped at distributor stock. It deliberately doesn't push volume above recent orders. Overstocking a kirana creates returns and credit problems, so basket growth comes from assortment instead.

**When to cross-sell.** Once per call, after the retailer has ordered their own items, never during a complaint, and only with something in stock. The pick follows the `Cross_Sell_Opportunity` column: 200 ml to 500 ml trade-up (pitched on price per 100 ml and shop margin), Saffola Gold replenishment, Livon/Set Wet or Saffola Oats as a new line with a small trial quantity (about ₹500). For "no clear opportunity" it uses preferred brands the shop has never bought.

**When to escalate.** If the retailer asks for a person, wants a discount, scheme or credit, complains about a past order, or the same item fails validation three times. Escalations become tasks for the sales representative, with the call ID and a summary.

## Guardrails

| Scenario | What happens | Enforced by |
|---|---|---|
| Wrong person answers | no history shared, call ends | brief and history locked until `confirm_identity(true)` |
| Out-of-stock item | refused, alternative offered | engine stock check |
| More than distributor stock | refused with the available count | engine, with per-call stock holds so two calls can't sell the same units |
| "10 boxes" | refused, agent asks for pieces | no box size exists in the data, so boxes are never converted |
| "A few more than last time" | refused, agent asks for a number | quantity reader flags vague and relative amounts |
| Quantity doesn't match the retailer's words | refused, agent checks | engine compares the number against the retailer's own words |
| Unusually large quantity | agent repeats it once | engine compares with history (over 3x the usual order) |
| Unclear audio | agent asks to repeat | `[unclear]` turns can't add items |
| Change after read-back | must read back again | order revision vs read-back revision |
| "Ruko" / "nahi" at confirmation | not treated as yes | multilingual yes/no check |
| Discount demand | standard price only, rep follow-up | no price field exists in any tool |
| Do not call again | opt-out stored, future calls blocked | `runs/do_not_call.jsonl`, checked by planner and API |
| "10 litre" of Saffola Gold | converted to packs, cheaper pack mix mentioned | unit conversion and pack check |

## Outputs

Confirmed orders are appended to `runs/orders.jsonl`:

```json
{
  "order_id": "SO-261127-0001",
  "idempotency_key": "1f5a3a30:1",
  "retailer_id": "R003",
  "distributor_id": "D001",
  "lines": [{"sku": "P001", "product": "Parachute Coconut Oil 500 ml", "quantity": 15,
             "unit": "piece", "unit_price_inr": 176, "line_value_inr": 2640, "source": "retailer_request"}],
  "order_value_inr": 2640,
  "pricing_basis": "distributor_billing_price",
  "confirmation": {"status": "confirmed", "retailer_said": "haan confirm kar do", "read_back_revision": 1},
  "language": "hi-IN"
}
```

Set `OMS_WEBHOOK_URL` and every confirmed order is also POSTed there with an `Idempotency-Key` header. `/api/oms/orders` is a mock distributor endpoint that validates the record, rejects unconfirmed orders or totals that don't add up, and treats a repeated key as a duplicate. Tasks (callbacks, complaints, escalations), market insights, opt-outs and a call log with transcripts go to the other files in `runs/`.

## What the data says

`python -m app.audit` prints these; the app shows them under Data notes.

- R001 Sharma Traders, the shop in the brief's sample call, is on the do-not-call list. Its history doesn't match the script either: it has never bought Parachute 500 ml, and its last 200 ml order was 3. It also buys Saffola Gold in 5 litre packs, so "10 litre" really means 2 x 5 litre (₹1,640) or 10 x 1 litre (₹1,680).
- 20 shop names are shared by 42 retailers, so calls are keyed on retailer ID, never on name.
- 188 of the 1,000 buying lines need more than the distributor holds, and 80 are for out-of-stock SKUs.
- Every history row points to 20 Nov 2026 as "today"; the planner uses that date.
- No pieces-per-box data exists.
- The README sheet says 100 inventory rows and 20 scenarios; the workbook has 200 and 19.

## Tests and evals

```bash
pip install -r requirements-dev.txt
pytest -q
```

The test suite runs offline. It covers every row of `Call_Scenarios`, the six surprise scenarios, unit and quantity parsing in Hindi, English and Tamil, stock holds across two calls, and the API. It also checks the Gemini, OpenAI and Sarvam request formats against mocked HTTP.

Conversation evals run the real model against a second model playing the shopkeeper, then check the final records:

```bash
python -m evals.run                # all 24 cases
python -m evals.run --only CS07,CS13
python -m evals.run --tag surprise --repeat 3
python -m evals.run --judge        # adds 1-5 scores for naturalness, language, respect, accuracy
```

Results go to `evals/report.md`, with full transcripts and tool calls in `evals/out/`. On the Gemini free tier a full run takes a while because of rate limits; the runner backs off and retries.

## Layout

```
app/
  data.py        workbook loader and models
  audit.py       data checks
  planner.py     who to call, what and how much to suggest, cross-sell pick
  catalog.py     product search from what the retailer said (English, Hindi, Tamil names)
  quantities.py  numbers, units, boxes, vague amounts, yes/no in several languages
  inventory.py   distributor stock with per-call holds
  order.py       order state: draft, read back, confirmed
  session.py     one call: tools, guardrails, outcome records
  agent.py       model loop: tools in, spoken line out
  prompts.py     system prompt
  llm.py         Gemini (native API) and OpenAI-compatible clients
  speech.py      Sarvam speech-to-text and text-to-speech
  languages.py   reply language from script, TTS fallbacks
  server.py      FastAPI app
web/             softphone and ops console (plain JS, no build step)
evals/           scenario cases and runner
tests/           offline test suite
```

## Known limits

- The demo is turn-based over HTTP, so a reply takes a couple of seconds. Production would stream audio both ways over telephony (Exotel or Plivo into Pipecat or LiveKit) to get under a second.
- Sarvam's TTS has no Assamese voice. Assamese replies are spoken with the Bengali voice, which shares the script; speech recognition does support Assamese.
- Stock is the workbook snapshot. Holds and confirmed orders live in memory and reset when the server restarts; orders, tasks and opt-outs are kept in `runs/`.
- Prices are the distributor billing prices from the workbook. There is no scheme or discount engine, on purpose.
