from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.order import CONFIRMED

Check = Callable[[object, object], tuple[bool, str]]


@dataclass
class Case:
    id: str
    title: str
    retailer_id: str
    goal: str
    checks: list[Check]
    unclear_turns: tuple[int, ...] = ()
    max_turns: int = 14
    tags: tuple[str, ...] = field(default_factory=tuple)


def _orders(ctx, session):
    return [o for o in ctx.store.list("orders") if o["call_id"] == session.id]


def order_is(expected: dict[str, int]) -> Check:
    def check(session, ctx):
        orders = _orders(ctx, session)
        if session.order.state != CONFIRMED or not orders:
            return False, f"no confirmed order (state {session.order.state})"
        got = {line["sku"]: line["quantity"] for line in orders[0]["lines"]}
        return got == expected, f"order {got}, expected {expected}"
    return check


def order_has(sku: str, quantity: int | None = None) -> Check:
    def check(session, ctx):
        orders = _orders(ctx, session)
        lines = {l["sku"]: l["quantity"] for l in orders[0]["lines"]} if orders else {}
        ok = sku in lines and (quantity is None or lines[sku] == quantity)
        return ok, f"order lines {lines}"
    return check


def no_order() -> Check:
    def check(session, ctx):
        orders = _orders(ctx, session)
        return not orders, "no order" if not orders else f"unexpected order {orders[0]['order_id']}"
    return check


def never_ordered(*skus: str) -> Check:
    def check(session, ctx):
        bad = [l["sku"] for o in _orders(ctx, session) for l in o["lines"] if l["sku"] in skus]
        return not bad, "none of the blocked SKUs ordered" if not bad else f"ordered {bad}"
    return check


def task(kind: str, category: str | None = None) -> Check:
    def check(session, ctx):
        tasks = [t for t in ctx.store.list("tasks") if t["call_id"] == session.id and t["kind"] == kind]
        if category:
            tasks = [t for t in tasks if t.get("category") == category]
        return bool(tasks), f"{len(tasks)} {kind} task(s)"
    return check


def opted_out() -> Check:
    def check(session, ctx):
        ok = session.retailer.id in ctx.store.opted_out()
        return ok, "opt-out recorded" if ok else "no opt-out recorded"
    return check


def insight() -> Check:
    def check(session, ctx):
        found = [i for i in ctx.store.list("insights") if i["call_id"] == session.id]
        return bool(found), f"{len(found)} insight(s)"
    return check


def nothing_disclosed() -> Check:
    def check(session, ctx):
        ok = session.identity is False and not session.brief_shared and not session.order.lines
        return ok, f"identity={session.identity}, brief shared={session.brief_shared}"
    return check


def spoke(language: str) -> Check:
    def check(session, ctx):
        turns = [t["language"] for t in session.transcript if t["speaker"] == "agent"]
        return language in turns[1:], f"agent languages {turns}"
    return check


def mentioned(word: str) -> Check:
    def check(session, ctx):
        said = " ".join(t["text"] for t in session.transcript if t["speaker"] == "agent").lower()
        return word.lower() in said, f"'{word}' {'mentioned' if word.lower() in said else 'not mentioned'}"
    return check


def basket_edited() -> Check:
    def check(session, ctx):
        outcome = session.basket_outcome()
        ok = outcome == "edited" and session.order.state == CONFIRMED
        return ok, f"usual basket {outcome}, order {session.order.state}"
    return check


def basket_taken() -> Check:
    def check(session, ctx):
        outcome = session.basket_outcome()
        return outcome in ("accepted", "accepted_plus"), f"usual basket {outcome}"
    return check


def call_ended() -> Check:
    def check(session, ctx):
        return session.outcome not in ("", "hung_up"), f"outcome {session.outcome or 'none'}"
    return check


CONFIRM = "When the agent reads back the order and asks to confirm, confirm it clearly."

CASES = [
    Case("CS01", "Interested retailer", "R003",
         f"Your Parachute 500 ml stock is low. Order 15 pieces of Parachute 500 ml and nothing else; politely refuse "
         f"other suggestions. {CONFIRM}",
         [order_is({"P001": 15}), call_ended()]),
    Case("CS02", "Busy retailer", "R003",
         "You are busy with customers. Ask them to call back after 5 PM. Do not order anything now.",
         [task("callback"), no_order(), call_ended()]),
    Case("CS03", "Out-of-stock SKU", "R043",
         "You want 10 Saffola Gold 1 litre. If it is not available, ask what else they have, but decide not to order "
         "anything today and end politely.",
         [never_ordered("P006", "P007"), call_ended()]),
    Case("CS04", "Quantity changed", "R003",
         f"Order 10 pieces of Parachute 500 ml. In your next reply, change it: make it 15, not 10. Refuse other "
         f"suggestions. {CONFIRM}",
         [order_is({"P001": 15})]),
    Case("CS05", "Language switch", "R003",
         f"Start in Hindi. From your second reply onwards, speak only English (say 'let's talk in English'). Order 12 "
         f"pieces of Parachute 500 ml, nothing else. {CONFIRM}",
         [order_is({"P001": 12}), spoke("en-IN")], tags=("surprise",)),
    Case("CS06", "Previous order complaint", "R003",
         "Complain that your last Parachute 500 ml delivery came 4 pieces short of what was billed. You want it fixed. "
         "You will not order anything until that is sorted.",
         [task("complaint"), no_order()], tags=("surprise",)),
    Case("CS07", "Box versus piece", "R003",
         f"Ask for 10 boxes of Parachute 500 ml. If asked how many pieces that is, say 2 boxes of 12, so 24 pieces "
         f"total. {CONFIRM}",
         [order_is({"P001": 24})], tags=("surprise",)),
    Case("CS08", "Discount request", "R003",
         f"Order 10 Parachute 500 ml but insist on an extra 10% discount. If they refuse, grumble and accept the normal "
         f"price. {CONFIRM}",
         [order_is({"P001": 10}), task("escalation", "pricing_or_discount")]),
    Case("CS09", "Not interested", "R003",
         "You do not want to order anything today. Decline politely but firmly.",
         [no_order(), call_ended()]),
    Case("CS10", "Human request", "R003",
         "You want to speak to your regular sales representative, not a machine. Ask for a human.",
         [task("escalation", "human_request"), no_order()]),
    Case("CS11", "Ambiguous quantity", "R003",
         f"Say 'send a few more Parachute 500 ml than last time'. Only if asked for an exact number, say 8. "
         f"{CONFIRM}",
         [order_is({"P001": 8})]),
    Case("CS12", "SKU ambiguity", "R003",
         f"Ask for 'Parachute small bottle, 10 pieces'. If asked which one, say the plain coconut oil 200 ml. "
         f"{CONFIRM}",
         [order_is({"P002": 10})]),
    Case("CS13", "Stock constraint", "R003",
         f"Ask for 20 Saffola Gold 1 litre. If told fewer are available, take all that are available. Nothing else. "
         f"{CONFIRM}",
         [order_is({"P006": 13})]),
    Case("CS14", "Cross-sell opportunity", "R064",
         f"Order 10 Parachute 500 ml. If the agent suggests one more product, agree to the quantity they suggest. "
         f"{CONFIRM}",
         [order_has("P001", 10), mentioned("Saffola")]),
    Case("CS15", "Wrong retailer", "R003",
         "You are not this shop. This is a medical store and you have never heard of them. Say it is a wrong number.",
         [nothing_disclosed(), no_order()]),
    Case("CS16", "Do not call again", "R003",
         "You are annoyed by these calls. Tell them clearly never to call this number again.",
         [opted_out(), no_order()]),
    Case("CS17", "Order summary correction", "R003",
         "Order 10 Parachute 500 ml and 5 Set Wet Hair Gel 250 g. When the agent reads the order back, correct the gel "
         "to 8. When they read back the corrected order, confirm.",
         [order_is({"P001": 10, "P014": 8})]),
    Case("CS18", "Unclear speech", "R003",
         f"Order 15 Parachute 500 ml. If the agent asks you to repeat, repeat it clearly. {CONFIRM}",
         [order_is({"P001": 15})], unclear_turns=(1,)),
    Case("CS19", "No order but demand signal", "R003",
         "You are fully stocked. Mention that a rival coconut oil brand is running a buy 2 get 1 free offer in your "
         "area. Do not order.",
         [insight(), no_order()]),
    Case("B01", "Usual basket, one yes", "R003",
         "You are busy but happy to reorder. If the agent offers your usual order, say yes to it as it is. If they "
         "suggest one extra product, politely decline. Confirm the order.",
         [basket_taken(), call_ended()], tags=("basket",)),
    Case("B02", "Usual basket, one change", "R005",
         "Speak Gujarati. If the agent offers your usual order, accept it but ask to drop the last item they listed. "
         "Decline any extra product. Confirm the corrected order.",
         [basket_edited(), call_ended()], tags=("basket", "language")),
    Case("S06", "Refuses order, agrees to callback", "R003",
         "Say you don't want to order today. If they offer to call back later, agree to a call tomorrow morning.",
         [no_order(), task("callback")], tags=("surprise",)),
    Case("L-TA", "Tamil, stock-limited item", "R011",
         f"Speak Tamil. Order 29 Parachute 200 ml, as last time. If fewer are available, take what is available. "
         f"{CONFIRM}",
         [order_is({"P002": 10}), spoke("ta-IN")], tags=("language",)),
    Case("L-MR", "Marathi, out-of-stock serum", "R008",
         f"Speak Marathi. Ask for 7 Livon Serum 100 ml. If it is unavailable and they offer the 50 ml, take 7 of those. "
         f"{CONFIRM}",
         [never_ordered("P012"), order_is({"P013": 7}), spoke("mr-IN")], tags=("language",)),
    Case("L-GU", "Gujarati order", "R005",
         f"Speak Gujarati. Order 18 Parachute 500 ml, nothing else. {CONFIRM}",
         [order_is({"P001": 18}), spoke("gu-IN")], tags=("language",)),
    Case("L-AS", "Assamese order", "R020",
         f"Speak Assamese. Order 7 Livon Serum 100 ml, nothing else. {CONFIRM}",
         [order_is({"P012": 7}), spoke("as-IN")], tags=("language",)),
]
