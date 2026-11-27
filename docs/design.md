# Design notes

How the agent decides things, what it refuses to do, and what it sends out. The README covers setup and the demo.

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

Each retailer turn is transcribed with its detected language and passed to the agent. The reply is spoken in whatever language the agent answered in. The agent has 16 tools (`app/tools.py`) and can only change the order through them. It can't set a price, add an item without the retailer's own words, or book an order that hasn't been read back since the last change. When a tool refuses, the reason goes back to the model, which then asks the retailer again.

## Business logic

**When to call.** Every retailer gets a brief (`app/planner.py`). A shop is called only if it isn't on the do-not-call list (the data flag, or an opt-out recorded on an earlier call) and has at least one in-stock item that is due. The queue is ordered by preferred calling window, then by value at stake: the value of the usual basket, weighted up when items are overdue. On the sample data that gives 97 calls; R001, R025 and R082 are skipped.

**The usual basket.** Right after the identity check, the agent offers the whole usual order in one turn, for example "Set Wet Deodorant 14, Saffola Oats 1 kg 8, Set Wet Hair Gel 20, total ₹6,704. Shall I send it as it is, or change anything?" A shopkeeper with customers at the counter can say one yes, and the call drops from 8 to 10 turns to about 4. Stock for the basket is held as soon as it is offered. The offer counts as the read-back, so a yes books it; any change needs a fresh read-back. Each call records whether the basket was taken as offered, taken with an add-on, edited or declined, and the Outcomes tab shows the acceptance rate.

**What goes in it.** An item is overdue once the days since its last order pass the shop's purchase cycle, and due soon at 75% of it. Overdue items come first, then the highest value, and items stop being added once they cover the shop's typical order value (at most four). Out-of-stock items are never offered. If the retailer asks for one, the engine returns an in-stock alternative from the same product family, brand or category.

**How much.** Quantities follow what the shop actually ordered. If the last order moved more than 20% from the one before, the suggestion follows the last order; otherwise it is the average of the two. It is capped at distributor stock. I kept it from pushing volume above recent orders, since overstocking a kirana leads to returns and credit problems; the cross-sell is where the order grows.

**When to cross-sell.** Once per call, in one short line after the retailer says yes to the basket, never during a complaint, and only with something in stock. The pick follows the `Cross_Sell_Opportunity` column: a 200 to 500 ml trade-up (pitched on price per 100 ml and shop margin), a Saffola Gold top-up, or Livon / Set Wet / Saffola Oats as a new line with a small trial (about ₹500). Where the column says "no clear opportunity", it uses a preferred brand the shop has never bought.

**When to hand over.** If the retailer asks for a person, wants a discount, scheme or credit, complains about a past order, or the same item fails validation three times. Each hand-over becomes a task for the sales representative with the call ID and a summary.

## Guardrails

| Situation | What happens | Enforced by |
|---|---|---|
| Wrong person answers | nothing shared, call ends | brief and history locked until `confirm_identity(true)` |
| Out-of-stock item | refused, alternative offered | engine stock check |
| More than distributor stock | refused with the available count | per-call stock holds, so two calls can't sell the same units |
| "10 boxes" | refused, agent asks for pieces | there is no box size in the data, so boxes are never converted |
| "A few more than last time" | refused, agent asks for a number | quantity reader flags vague and relative amounts |
| Number doesn't match the retailer's words | refused, agent checks | engine reads the retailer's own words |
| Unusually large quantity | agent repeats it once | compared with history (over 3x the usual order) |
| Unclear audio | agent asks to repeat | `[unclear]` turns can't add items |
| Change after read-back | must read back again | order revision vs read-back revision |
| "Ruko" or "nahi" at confirmation | not taken as a yes | multilingual yes/no check |
| Discount demand | standard price, rep follows up | no tool takes a price |
| "Don't call again" | opt-out stored, future calls blocked | `runs/do_not_call.jsonl`, checked by the planner and the API |
| "10 litre" of Saffola Gold | converted to packs, cheaper pack mix mentioned | unit conversion and pack check |

## What goes out

Confirmed orders are appended to `runs/orders.jsonl`:

```json
{
  "order_id": "SO-261127-0001",
  "idempotency_key": "68e1d032:4",
  "retailer_id": "R003",
  "distributor_id": "D001",
  "lines": [
    {"sku": "P015", "product": "Set Wet Deodorant 150 ml", "quantity": 14, "unit": "piece",
     "unit_price_inr": 176, "line_value_inr": 2464, "source": "suggested"},
    {"sku": "P010", "product": "Saffola Oats 1 kg", "quantity": 8, "unit": "piece",
     "unit_price_inr": 280, "line_value_inr": 2240, "source": "suggested"},
    {"sku": "P014", "product": "Set Wet Hair Gel 250 g", "quantity": 20, "unit": "piece",
     "unit_price_inr": 100, "line_value_inr": 2000, "source": "suggested"},
    {"sku": "P013", "product": "Livon Serum 50 ml", "quantity": 6, "unit": "piece",
     "unit_price_inr": 84, "line_value_inr": 504, "source": "cross_sell"}
  ],
  "order_value_inr": 7208,
  "pricing_basis": "distributor_billing_price",
  "confirmation": {"status": "confirmed", "retailer_said": "haan confirm kar do", "read_back_revision": 4},
  "language": "hi-IN"
}
```

With `OMS_WEBHOOK_URL` set, every confirmed order is also POSTed there with an `Idempotency-Key` header, and failed deliveries are retried. `/api/oms/orders` is a mock distributor endpoint: it rejects unconfirmed orders and totals that don't add up, and treats a repeated key as a duplicate. Callbacks, complaints and escalations go to `runs/tasks.jsonl`; market insights, opt-outs and the call log (with transcripts and reply times) go to the other files in `runs/`.

## What the data says

`python -m app.audit` prints these; the app shows them under Data notes.

- R001 Sharma Traders, the shop in the brief's sample call, is on the do-not-call list. Its history doesn't match the script either: it has never bought Parachute 500 ml, and its last 200 ml order was 3. It also buys Saffola Gold in 5 litre packs, so "10 litre" means 2 x 5 litre (₹1,640) or 10 x 1 litre (₹1,680).
- 20 shop names are shared by 42 retailers, so calls are keyed on retailer ID, never on name.
- 188 of the 1,000 buying lines need more than the distributor holds, and 80 are for out-of-stock SKUs.
- Every history row points to 20 Nov 2026 as "today"; the planner uses that date.
- There is no pieces-per-box data.
- The README sheet says 100 inventory rows and 20 scenarios; the workbook has 200 and 19.
