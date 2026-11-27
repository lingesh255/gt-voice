from __future__ import annotations

import inspect
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .catalog import search
from .data import Dataset, Product
from .inventory import StockLedger
from .oms import OrderGateway
from .order import CONFIRMED, DRAFT, READ_BACK, Order, OrderError
from .outcomes import OutcomeStore, now_iso
from .planner import Brief, alternatives, build_brief
from .quantities import UnitError, is_explicit_yes, is_volume_unit, read_quantity, to_pieces
from .tools import ESCALATIONS, OUTCOMES, SOURCES, TOOL_NAMES

TIME_24H = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
REJECTIONS_BEFORE_HANDOFF = 3


class CallBlocked(Exception):
    pass


@dataclass
class Context:
    dataset: Dataset
    ledger: StockLedger
    store: OutcomeStore
    gateway: OrderGateway
    today: date | None = None

    @property
    def plan_date(self) -> date:
        return self.today or self.dataset.as_of


class CallSession:
    def __init__(self, ctx: Context, retailer_id: str, call_id: str | None = None):
        self.ctx = ctx
        self.id = call_id or uuid.uuid4().hex[:8]
        self.retailer = ctx.dataset.retailer(retailer_id)
        self.brief: Brief = build_brief(ctx.dataset, ctx.ledger, self.retailer.id, ctx.plan_date,
                                        ctx.store.opted_out())
        if self.brief.do_not_call:
            raise CallBlocked(f"{self.retailer.name} ({self.retailer.id}) is on the do-not-call list.")
        self.order = Order()
        self.identity: bool | None = None
        self.brief_shared = False
        self.language = self.retailer.language_code
        self.languages_used = {self.language}
        self.started_at = now_iso()
        self.ended = False
        self.outcome = ""
        self.order_id = ""
        self.tasks: list[str] = []
        self.complaint_open = False
        self.cross_sell_hinted = False
        self.rejections: Counter = Counter()
        self.transcript: list[dict] = []
        self.events: list[dict] = []
        self.recorded = False
        self.basket: dict | None = None

    @property
    def dist(self) -> str:
        return self.retailer.distributor_id

    def log(self, kind: str, **data) -> None:
        self.events.append({"t": now_iso(), "kind": kind, **data})

    def add_turn(self, speaker: str, text: str, language: str = "") -> None:
        if language:
            self.languages_used.add(language)
            if speaker == "agent":
                self.language = language
        self.transcript.append({"t": now_iso(), "speaker": speaker, "text": text, "language": language})

    def call_tool(self, name: str, args: dict | None) -> dict:
        args = args or {}
        if name not in TOOL_NAMES:
            result = {"ok": False, "error": f"Unknown tool {name}."}
        elif self.ended and name != "end_call":
            result = {"ok": False, "error": "The call has ended."}
        else:
            method = getattr(self, f"tool_{name}")
            try:
                inspect.signature(method).bind(**args)
            except TypeError as exc:
                result = {"ok": False, "error": f"Bad arguments for {name}: {exc}"}
            else:
                try:
                    result = method(**args)
                except (OrderError, UnitError) as exc:
                    result = {"ok": False, "error": str(exc)}
        self.log("tool", name=name, args=args, ok=result.get("ok", False),
                 error=result.get("error", ""), result=result)
        if not result.get("ok", False):
            self.log("guard", name=name, reason=result.get("error", ""))
        return result

    def _need_identity(self) -> dict | None:
        if self.identity is not True:
            return {"ok": False, "error": "Identity not confirmed yet. Confirm you are speaking to the right shop "
                                          "before discussing orders or history."}
        return None

    def _product(self, sku: str) -> Product:
        try:
            return self.ctx.dataset.product(str(sku))
        except Exception:
            raise OrderError(f"Unknown SKU {sku!r}. Use find_products to look it up.") from None

    def _reject(self, sku: str, message: str, **extra) -> dict:
        self.rejections[sku] += 1
        result = {"ok": False, "error": message, **extra}
        if self.rejections[sku] >= REJECTIONS_BEFORE_HANDOFF:
            result["next"] = ("This item has failed several times. Offer to have a sales representative call back "
                              "about it (escalate_to_human) instead of trying again.")
        return result

    def _stock_view(self, product: Product) -> dict:
        free = self.ctx.ledger.available(self.dist, product.sku, self.id)
        return {"available_units": free, "status": self.ctx.ledger.status(self.dist, product.sku, self.id)}

    def _history(self, sku: str):
        return self.ctx.dataset.purchase(self.retailer.id, sku)

    def _check_words(self, product: Product, pieces: int, said: str, verified: bool) -> str | None:
        history = self._history(product.sku)
        reading = read_quantity(
            said, (product.pack_amount, product.pack_measure), self.ctx.dataset.pack_sizes(),
            history.last_qty if history else None,
        )
        if reading.unclear:
            return ("The retailer's words were unclear. Ask them to repeat the product and quantity. "
                    "Do not guess.")
        if verified:
            return None
        if reading.box:
            return ("The retailer mentioned boxes or cartons. Box sizes are not in the product master, so do "
                    "not convert. Ask how many pieces they want, then retry with verified=true.")
        if reading.unconvertible:
            return (f"The amount they said is not a whole number of {product.pack_size} packs. Ask how many "
                    f"pieces they want.")
        if reading.vague and not reading.counts:
            return (f"The quantity is not exact ('{reading.vague_word}'). Ask for a number. Do not infer one, "
                    f"even from last time.")
        if reading.counts and pieces not in reading.counts:
            heard = ", ".join(str(c) for c in reading.counts)
            return (f"The retailer's words suggest {heard} pieces, but you passed {pieces}. Check with the "
                    f"retailer, then retry (verified=true once they confirm).")
        usual = max(history.last_qty, history.previous_qty, history.avg_monthly_qty) if history else 0
        limit = max(3 * usual, usual + 20) if usual else 60
        if pieces > limit:
            return (f"{pieces} pieces is far above what they usually take ({usual or 'no history'}). Repeat the "
                    f"number back to them once, then retry with verified=true if they confirm.")
        return None

    def _pack_tip(self, product: Product, unit: str, said: str, pieces: int) -> str:
        reading = read_quantity(said, (product.pack_amount, product.pack_measure), self.ctx.dataset.pack_sizes())
        if not (is_volume_unit(unit) or reading.volume):
            return ""
        total = pieces * product.pack_amount
        best = None
        for other in self.ctx.dataset.family(product):
            if other.sku == product.sku or total % other.pack_amount:
                continue
            count = total // other.pack_amount
            if self.ctx.ledger.available(self.dist, other.sku, self.id) < count:
                continue
            cost = count * other.price
            if cost < pieces * product.price and (best is None or cost < best[1]):
                best = (other, cost, count)
        if not best:
            return ""
        other, cost, count = best
        amount = f"{total / 1000:g} {'litres' if product.pack_measure == 'ml' else 'kg'}"
        return (f"The same {amount} is also {count} x {other.label} for Rs {cost} instead of "
                f"Rs {pieces * product.price}. Mention it once; let the retailer choose.")

    def _next_after_add(self) -> str:
        cs = self.brief.cross_sell
        if cs and not self.cross_sell_hinted and not self.complaint_open and cs.sku not in self.order.lines:
            self.cross_sell_hinted = True
            return (f"When they have finished their own items, suggest {cs.product} once "
                    f"(about {cs.suggested_qty}): {cs.pitch} If they say no, drop it.")
        return "Ask if they need anything else. When they are done, call read_back_order."

    def tool_confirm_identity(self, is_right_shop: bool, note: str = "") -> dict:
        self.identity = bool(is_right_shop)
        self.log("identity", confirmed=self.identity, note=note)
        if self.identity:
            return {"ok": True, "next": "Call get_call_brief, then ask how their stock is."}
        return {"ok": True, "next": "Apologise for the wrong number. Do not share any shop or order details. "
                                    "End the call with end_call(outcome='wrong_person')."}

    def tool_get_call_brief(self) -> dict:
        blocked = self._need_identity()
        if blocked:
            return blocked
        self.brief_shared = True
        return {
            "ok": True,
            "brief": self.brief.for_agent(),
            "guidance": ("Lead with lead_with items one or two at a time: remind them of last_qty and propose "
                         "suggest_qty. Mention also_buys only if they want more. Never offer items in "
                         "not_available_today; if asked, offer the alternative. Keep the cross_sell until they "
                         "have ordered their own items."),
        }

    def tool_find_products(self, query: str) -> dict:
        result = search(self.ctx.dataset, str(query))
        matches = []
        for product in result.matches[:6]:
            entry = {
                "sku": product.sku,
                "product": product.label,
                "price_inr": product.price,
                "mrp_inr": product.mrp,
                "unit": product.selling_unit,
                **self._stock_view(product),
            }
            if self.identity is True:
                history = self._history(product.sku)
                entry["retailer_last_qty"] = history.last_qty if history else None
            matches.append(entry)
        reply = {"ok": True, "matches": matches, "ambiguous": result.ambiguous}
        if result.note:
            reply["note"] = result.note
        return reply

    def tool_check_stock(self, sku: str) -> dict:
        product = self._product(sku)
        view = self._stock_view(product)
        reply = {"ok": True, "sku": product.sku, "product": product.label, **view}
        if view["available_units"] <= 0:
            reply["alternatives"] = [
                {"sku": p.sku, "product": p.label, **self._stock_view(p)}
                for p in alternatives(self.ctx.dataset, self.ctx.ledger, self.dist, product)
            ]
        return reply

    def _validate_line(self, product: Product, quantity, unit: str, said: str, verified: bool,
                       current: int = 0) -> tuple[int | None, dict | None]:
        try:
            pieces = to_pieces(float(quantity), unit or "piece", (product.pack_amount, product.pack_measure))
        except (TypeError, ValueError) as exc:
            return None, self._reject(product.sku, str(exc))
        if pieces < product.moq:
            return None, self._reject(product.sku, f"Minimum order for {product.label} is {product.moq}.")
        problem = self._check_words(product, pieces, said, bool(verified))
        if problem:
            return None, self._reject(product.sku, problem)
        free = self.ctx.ledger.available(self.dist, product.sku, self.id)
        if free <= 0:
            options = alternatives(self.ctx.dataset, self.ctx.ledger, self.dist, product)
            return None, self._reject(
                product.sku,
                f"{product.label} is out of stock at the distributor today. Do not add it. Say so plainly and, "
                f"only if it fits, offer one alternative without pressure.",
                alternatives=[{"sku": p.sku, "product": p.label, **self._stock_view(p)} for p in options],
            )
        if pieces > free:
            return None, self._reject(
                product.sku,
                f"Only {free} pieces of {product.label} are available today; they asked for {pieces}. Tell them "
                f"and ask whether to take {free} now (the rest can follow on the next order) or change it.",
                available_units=free,
            )
        return pieces, None

    def tool_add_to_order(self, sku: str, quantity, unit: str = "piece", retailer_said: str = "",
                          source: str = "retailer_request", verified: bool = False) -> dict:
        blocked = self._need_identity()
        if blocked:
            return blocked
        product = self._product(sku)
        if product.sku in self.order.lines:
            raise OrderError(f"{product.label} is already in the order with {self.order.lines[product.sku].quantity}. "
                             f"Use change_quantity with the new total.")
        pieces, problem = self._validate_line(product, quantity, unit, retailer_said, verified)
        if problem:
            return problem
        ok, left = self.ctx.ledger.hold(self.dist, product.sku, self.id, pieces)
        if not ok:
            return self._reject(product.sku, f"Stock changed: only {left} pieces left now.", available_units=left)
        line = self.order.add(product, pieces, source if source in SOURCES else "retailer_request", retailer_said)
        self.ctx.ledger.refresh(self.id)
        self.log("order", action="add", sku=product.sku, quantity=pieces)
        reply = {"ok": True, "added": line.as_dict(), "order_total_inr": self.order.total,
                 "lines_in_order": len(self.order.lines), "next": self._next_after_add()}
        if left <= self.ctx.dataset.low_stock_threshold:
            reply["stock_note"] = f"Only {left} left at the distributor after this."
        tip = self._pack_tip(product, unit, retailer_said, pieces)
        if tip:
            reply["pack_tip"] = tip
        return reply

    def tool_change_quantity(self, sku: str, quantity, unit: str = "piece", retailer_said: str = "",
                             verified: bool = False) -> dict:
        blocked = self._need_identity()
        if blocked:
            return blocked
        product = self._product(sku)
        if product.sku not in self.order.lines:
            raise OrderError(f"{product.label} is not in the order yet. Use add_to_order.")
        pieces, problem = self._validate_line(product, quantity, unit, retailer_said, verified)
        if problem:
            return problem
        ok, left = self.ctx.ledger.hold(self.dist, product.sku, self.id, pieces)
        if not ok:
            return self._reject(product.sku, f"Only {left} pieces available now.", available_units=left)
        old = self.order.lines[product.sku].quantity
        was_read_back = self.order.state == READ_BACK
        line = self.order.change(product.sku, pieces, retailer_said)
        self.log("order", action="change", sku=product.sku, quantity=pieces, previous=old)
        reply = {"ok": True, "changed": line.as_dict(), "previous_quantity": old, "order_total_inr": self.order.total}
        if was_read_back:
            reply["next"] = "The order changed after the read-back. Call read_back_order and read the full order again."
        return reply

    def tool_remove_from_order(self, sku: str) -> dict:
        blocked = self._need_identity()
        if blocked:
            return blocked
        product = self._product(sku)
        line = self.order.remove(product.sku)
        self.ctx.ledger.release(self.dist, product.sku, self.id)
        self.log("order", action="remove", sku=product.sku)
        return {"ok": True, "removed": line.as_dict(), "order_total_inr": self.order.total}

    def tool_propose_basket(self) -> dict:
        blocked = self._need_identity()
        if blocked:
            return blocked
        if not self.brief_shared:
            return {"ok": False, "error": "Call get_call_brief first."}
        if self.basket:
            return {"ok": False, "error": "The usual basket was already offered on this call."}
        skipped = []
        for item in self.brief.lead:
            if item.sku in self.order.lines:
                continue
            product = self.ctx.dataset.product(item.sku)
            quantity = min(item.suggested_qty, self.ctx.ledger.available(self.dist, item.sku, self.id))
            if quantity < product.moq:
                skipped.append(product.label)
                continue
            ok, _ = self.ctx.ledger.hold(self.dist, item.sku, self.id, quantity)
            if not ok:
                skipped.append(product.label)
                continue
            self.order.add(product, quantity, "suggested")
        if not self.order.lines:
            return {"ok": False, "error": "Nothing in their usual basket is in stock today. Ask what they need instead."}
        self.ctx.ledger.refresh(self.id)
        self.order.read_back()
        self.basket = {
            "lines": {sku: line.quantity for sku, line in self.order.lines.items()},
            "value": self.order.total,
            "revision": self.order.revision,
        }
        self.log("order", action="basket", value=self.order.total, lines=len(self.order.lines))
        reply = {
            "ok": True,
            "basket": [{"product": l.product.label, "quantity": l.quantity, "unit": l.product.selling_unit}
                       for l in self.order.lines.values()],
            "total_inr": self.order.total,
            "next": "Offer this as their usual order in one turn: each line with its quantity, then the total, then ask "
                    "whether to send it as it is or change anything. This counts as the read-back, so a clear yes can "
                    "go straight to confirm_order.",
        }
        if skipped:
            reply["left_out"] = f"Not in stock today, so left out: {', '.join(skipped)}."
        return reply

    def basket_outcome(self) -> str:
        if not self.basket:
            return "not_offered"
        if self.order.state != CONFIRMED:
            return "declined"
        final = {sku: line.quantity for sku, line in self.order.lines.items()}
        if final == self.basket["lines"]:
            return "accepted"
        if all(final.get(sku) == qty for sku, qty in self.basket["lines"].items()):
            return "accepted_plus"
        return "edited"

    def tool_read_back_order(self) -> dict:
        blocked = self._need_identity()
        if blocked:
            return blocked
        self.order.read_back()
        self.log("order", action="read_back", revision=self.order.revision)
        return {
            "ok": True,
            "lines": [{"product": l.product.label, "quantity": l.quantity, "unit": l.product.selling_unit,
                       "line_value_inr": l.value} for l in self.order.lines.values()],
            "total_inr": self.order.total,
            "next": "Read every line and the total back, then ask for a clear yes. If they change anything, update "
                    "it and read back again.",
        }

    def tool_confirm_order(self, retailer_said: str) -> dict:
        blocked = self._need_identity()
        if blocked:
            return blocked
        if self.order.state != READ_BACK or self.order.read_back_revision != self.order.revision:
            return {"ok": False, "error": "The order has not been read back since the last change. Call "
                                          "read_back_order and read the whole order to the retailer first."}
        if not is_explicit_yes(retailer_said):
            return {"ok": False, "error": f"'{retailer_said}' is not a clear yes. Ask them plainly whether to "
                                          f"place the order."}
        quantities = {sku: line.quantity for sku, line in self.order.lines.items()}
        ok, short = self.ctx.ledger.commit(self.dist, self.id, quantities)
        if not ok:
            details = ", ".join(f"{sku}: {free} left" for sku, free in short.items())
            return {"ok": False, "error": f"Stock moved while we were talking ({details}). Tell the retailer, "
                                          f"update those lines and read back again."}
        self.order.confirm()
        payload = self.order_payload(retailer_said)
        self.ctx.gateway.record(payload)
        self.order_id = payload["order_id"]
        self.log("order", action="confirm", order_id=self.order_id, total=self.order.total)
        return {"ok": True, "order_id": self.order_id, "total_inr": self.order.total,
                "next": "Thank them, say the distributor will deliver as usual, and close politely with end_call."}

    def order_payload(self, said: str) -> dict:
        r = self.retailer
        stamp = now_iso()
        return {
            "order_id": self.ctx.store.next_order_id(),
            "idempotency_key": f"{self.id}:{self.order.revision}",
            "created_at": stamp,
            "channel": "ai_voice_agent",
            "call_id": self.id,
            "retailer_id": r.id,
            "retailer_name": r.name,
            "retailer_contact_id": r.contact_id,
            "city": r.city,
            "distributor_id": r.distributor_id,
            "currency": "INR",
            "pricing_basis": "distributor_billing_price",
            "lines": [line.as_dict() for line in self.order.lines.values()],
            "order_value_inr": self.order.total,
            "confirmation": {
                "status": "confirmed",
                "confirmed_at": stamp,
                "retailer_said": said,
                "read_back_revision": self.order.read_back_revision,
            },
            "language": self.language,
        }

    def _task(self, kind: str, **fields) -> dict:
        record = {
            "id": self.ctx.store.next_task_id(kind),
            "kind": kind,
            "status": "open",
            "call_id": self.id,
            "retailer_id": self.retailer.id,
            "retailer_name": self.retailer.name,
            "distributor_id": self.dist,
            **fields,
        }
        self.ctx.store.add("tasks", record)
        self.tasks.append(record["id"])
        self.log("task", task=record)
        return record

    def tool_schedule_callback(self, when: str, reason: str, day: str = "today", time_24h: str = "") -> dict:
        due = None
        if time_24h:
            match = TIME_24H.match(time_24h.strip())
            if not match:
                return {"ok": False, "error": "time_24h must look like 17:00."}
            base = datetime.now().astimezone()
            if day == "tomorrow":
                base += timedelta(days=1)
            due = base.replace(hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0)
        task = self._task("callback", when=when, day=day or "today",
                          due_at=due.isoformat(timespec="minutes") if due else None, reason=reason,
                          preferred_window=self.brief.window, assign_to="ai_agent")
        return {"ok": True, "callback_id": task["id"],
                "next": "Confirm the time back to them in one short sentence and close the call. Do not push an order."}

    def tool_log_complaint(self, summary: str, sku: str = "") -> dict:
        related = None
        if sku:
            product = self._product(sku)
            history = self._history(product.sku)
            related = {"sku": product.sku, "product": product.label}
            if history and self.identity is True:
                related.update(transaction_id=history.transaction_id, order_date=history.order_date.isoformat(),
                               billed_quantity=history.last_qty)
        task = self._task("complaint", summary=summary, related=related, priority="high",
                          assign_to="sales_representative")
        self.complaint_open = True
        return {"ok": True, "ticket_id": task["id"],
                "next": "Apologise once, give the ticket number, and say a sales representative will call them about "
                        "it. Do not argue or promise a refund. Then ask gently if they still want to order today."}

    def tool_escalate_to_human(self, category: str, summary: str) -> dict:
        if category not in ESCALATIONS:
            category = "other"
        task = self._task("escalation", category=category, summary=summary, assign_to="sales_representative")
        note = ""
        if category == "pricing_or_discount":
            note = " Do not offer or hint at any discount yourself; prices on this call are the standard ones."
        return {"ok": True, "escalation_id": task["id"],
                "next": "Tell them a sales representative will call back. Ask for a convenient time if they have "
                        "not given one." + note}

    def tool_record_do_not_call(self, reason: str) -> dict:
        self.ctx.store.add("opt_outs", {"retailer_id": self.retailer.id, "retailer_name": self.retailer.name,
                                        "call_id": self.id, "reason": reason})
        self.log("dnc", reason=reason)
        if self.order.is_open and self.order.lines:
            self.order.cancel()
        self.ctx.ledger.release_all(self.id)
        return {"ok": True, "next": "Apologise for the trouble, confirm they will not get these calls again, and end "
                                    "the call with end_call(outcome='do_not_call')."}

    def tool_log_market_insight(self, summary: str, competitor: str = "", brand: str = "") -> dict:
        record = self.ctx.store.add("insights", {
            "id": self.ctx.store.next_task_id("insight"),
            "call_id": self.id,
            "retailer_id": self.retailer.id,
            "city": self.retailer.city,
            "state": self.retailer.state,
            "summary": summary,
            "competitor": competitor,
            "brand": brand,
        })
        self.log("insight", insight=record)
        return {"ok": True, "insight_id": record["id"],
                "next": "Thank them for the information. Do not force an order."}

    def tool_end_call(self, outcome: str, note: str = "") -> dict:
        if outcome not in OUTCOMES:
            outcome = "no_order"
        if self.order.state == CONFIRMED:
            outcome = "order_placed"
        elif outcome == "order_placed":
            outcome = "no_order"
        self.finish(outcome, note)
        return {"ok": True, "outcome": outcome, "next": "Say a short, warm goodbye in one sentence."}

    def finish(self, outcome: str = "", note: str = "") -> None:
        if not self.ended:
            self.ended = True
            if self.order.state in (DRAFT, READ_BACK) and self.order.lines:
                self.order.cancel()
            self.ctx.ledger.release_all(self.id)
            if not outcome:
                if self.order.state == CONFIRMED:
                    outcome = "order_placed"
                elif self.identity is False:
                    outcome = "wrong_person"
                else:
                    outcome = "hung_up"
            self.outcome = outcome
            self.log("end", outcome=outcome, note=note)
        if not self.recorded:
            self.recorded = True
            self.ctx.store.add("calls", self.call_record())

    def call_record(self) -> dict:
        tools = [e for e in self.events if e["kind"] == "tool"]
        return {
            "call_id": self.id,
            "retailer_id": self.retailer.id,
            "retailer_name": self.retailer.name,
            "started_at": self.started_at,
            "ended_at": now_iso(),
            "outcome": self.outcome,
            "order_id": self.order_id or None,
            "order_value_inr": self.order.total if self.order_id else 0,
            "languages": sorted(self.languages_used),
            "turns": len(self.transcript),
            "tool_calls": len(tools),
            "guard_rejections": sum(1 for e in tools if not e["ok"]),
            "basket": {"outcome": self.basket_outcome(), "offered_value_inr": self.basket["value"] if self.basket else 0},
            "tasks": list(self.tasks),
            "transcript": self.transcript,
        }

    def snapshot(self) -> dict:
        order = self.order.summary()
        for line in order["lines"]:
            line["available_after"] = self.ctx.ledger.available(self.dist, line["sku"], self.id) - line["quantity"]
        return {
            "call_id": self.id,
            "retailer_id": self.retailer.id,
            "retailer_name": self.retailer.name,
            "identity": self.identity,
            "language": self.language,
            "languages_used": sorted(self.languages_used),
            "order": order,
            "order_id": self.order_id,
            "ended": self.ended,
            "outcome": self.outcome,
            "tasks": list(self.tasks),
            "basket": {"outcome": self.basket_outcome(), "offered_value_inr": self.basket["value"]} if self.basket else None,
            "brief": self.brief.as_dict(),
            "events": self.events[-60:],
            "transcript": self.transcript,
        }
