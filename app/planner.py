from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .data import PACK_FACTORS, PACK_PATTERN, Dataset, Product, Retailer
from .inventory import StockLedger

SURNAMES = {"Sharma", "Patel", "Gupta", "Kumar", "Agarwal", "Jain", "Shah", "Mehta", "Verma", "Singh"}
HONORIFIC_LANGUAGES = {"Hindi", "Marathi", "Gujarati"}
DUE_SOON = 0.75
TREND_BAND = 0.2
MAX_LEAD = 4
TRIAL_VALUE_INR = 500
BRAND_NAMES = ("Parachute", "Saffola", "Livon", "Set Wet", "Nihar")


def suggest_quantity(last: int, previous: int) -> tuple[int, str]:
    if previous > 0 and last >= previous * (1 + TREND_BAND):
        return max(1, last), f"rising: last {last}, before that {previous}"
    if previous > 0 and last <= previous * (1 - TREND_BAND):
        return max(1, last), f"slowing: last {last}, before that {previous}"
    return max(1, int((last + previous) / 2 + 0.5)), f"steady: last {last}, before that {previous}"


def due_status(days_since: int, cycle_days: int) -> str:
    ratio = days_since / max(cycle_days, 1)
    if ratio >= 1:
        return "overdue"
    if ratio >= DUE_SOON:
        return "due_soon"
    return "not_due"


@dataclass
class DueItem:
    sku: str
    product: str
    last_qty: int
    previous_qty: int
    avg_monthly_qty: int
    days_since: int
    status: str
    suggested_qty: int
    why: str
    available: int
    stock_status: str
    price: int
    alternative: str = ""

    @property
    def value(self) -> int:
        return self.suggested_qty * self.price

    def for_agent(self) -> dict:
        stock = "in stock" if self.stock_status == "Available" else f"low stock, only {self.available} left"
        return {
            "sku": self.sku,
            "product": self.product,
            "last_qty": self.last_qty,
            "previous_qty": self.previous_qty,
            "days_since_last_order": self.days_since,
            "status": self.status,
            "suggest_qty": self.suggested_qty,
            "why": self.why,
            "stock": stock,
        }


@dataclass
class CrossSell:
    sku: str
    product: str
    kind: str
    pitch: str
    suggested_qty: int
    price: int
    mrp: int

    def for_agent(self) -> dict:
        return {
            "sku": self.sku,
            "product": self.product,
            "kind": self.kind,
            "suggest_qty": self.suggested_qty,
            "price_inr": self.price,
            "mrp_inr": self.mrp,
            "pitch": self.pitch,
        }


@dataclass
class Brief:
    retailer: Retailer
    window: str
    cycle_days: int
    typical_order_value: int
    preferred_brands: tuple[str, ...]
    do_not_call: bool
    should_call: bool
    skip_reason: str
    priority: int
    expected_value: int
    address_as: str | None
    lead: list[DueItem] = field(default_factory=list)
    others: list[DueItem] = field(default_factory=list)
    not_due: list[DueItem] = field(default_factory=list)
    unavailable: list[DueItem] = field(default_factory=list)
    cross_sell: CrossSell | None = None
    next_due_in_days: int | None = None

    def for_agent(self) -> dict:
        return {
            "shop": self.retailer.name,
            "city": self.retailer.city,
            "address_as": self.address_as,
            "usual_cycle_days": self.cycle_days,
            "typical_order_inr": self.typical_order_value,
            "preferred_brands": list(self.preferred_brands),
            "lead_with": [item.for_agent() for item in self.lead],
            "also_buys": [
                {"sku": i.sku, "product": i.product, "last_qty": i.last_qty, "suggest_qty": i.suggested_qty,
                 "status": i.status}
                for i in self.others
            ],
            "not_available_today": [
                {"sku": i.sku, "product": i.product, "alternative": i.alternative or None}
                for i in self.unavailable
            ],
            "cross_sell": self.cross_sell.for_agent() if self.cross_sell else None,
        }

    def as_dict(self) -> dict:
        r = self.retailer
        return {
            "retailer_id": r.id,
            "name": r.name,
            "city": r.city,
            "state": r.state,
            "language": r.language,
            "language_code": r.language_code,
            "segment": r.segment,
            "outlet_type": r.outlet_type,
            "distributor_id": r.distributor_id,
            "window": self.window,
            "cycle_days": self.cycle_days,
            "typical_order_value": self.typical_order_value,
            "preferred_brands": list(self.preferred_brands),
            "do_not_call": self.do_not_call,
            "should_call": self.should_call,
            "skip_reason": self.skip_reason,
            "priority": self.priority,
            "expected_value": self.expected_value,
            "address_as": self.address_as,
            "next_due_in_days": self.next_due_in_days,
            "lead": [_item_dict(i) for i in self.lead],
            "others": [_item_dict(i) for i in self.others],
            "not_due": [_item_dict(i) for i in self.not_due],
            "unavailable": [_item_dict(i) for i in self.unavailable],
            "cross_sell": self.cross_sell.for_agent() if self.cross_sell else None,
        }


def _item_dict(item: DueItem) -> dict:
    return {
        "sku": item.sku,
        "product": item.product,
        "last_qty": item.last_qty,
        "previous_qty": item.previous_qty,
        "avg_monthly_qty": item.avg_monthly_qty,
        "days_since": item.days_since,
        "status": item.status,
        "suggested_qty": item.suggested_qty,
        "why": item.why,
        "available": item.available,
        "stock_status": item.stock_status,
        "value": item.value,
        "alternative": item.alternative,
    }


def alternatives(dataset: Dataset, ledger: StockLedger, distributor_id: str, product: Product) -> list[Product]:
    def free(p: Product) -> bool:
        return p.sku != product.sku and ledger.available(distributor_id, p.sku) > 0

    same_family = [p for p in dataset.family(product) if free(p)]
    same_brand = [p for p in dataset.products.values()
                  if free(p) and p.brand == product.brand and p.category == product.category and p not in same_family]
    same_category = [p for p in dataset.products.values()
                     if free(p) and p.category == product.category and p.brand != product.brand]
    ordered = same_family + sorted(same_brand, key=lambda p: p.sku) + sorted(same_category, key=lambda p: p.sku)
    return ordered[:2]


def build_brief(dataset: Dataset, ledger: StockLedger, retailer_id: str, today: date | None = None,
                opted_out: set[str] | frozenset = frozenset()) -> Brief:
    retailer = dataset.retailer(retailer_id)
    prefs = dataset.prefs(retailer.id)
    today = today or dataset.as_of
    shift = max(0, (today - dataset.as_of).days)
    dist = retailer.distributor_id

    items: list[DueItem] = []
    for line in dataset.purchases(retailer.id):
        product = dataset.product(line.sku)
        days = line.days_since + shift
        qty, why = suggest_quantity(line.last_qty, line.previous_qty)
        free = ledger.available(dist, product.sku)
        if 0 < free < qty:
            qty, why = free, f"{why}; capped at {free} (distributor stock)"
        item = DueItem(
            sku=product.sku, product=product.label, last_qty=line.last_qty, previous_qty=line.previous_qty,
            avg_monthly_qty=line.avg_monthly_qty, days_since=days, status=due_status(days, prefs.frequency_days),
            suggested_qty=qty, why=why, available=free, stock_status=ledger.status(dist, product.sku),
            price=product.price,
        )
        if free <= 0:
            alt = alternatives(dataset, ledger, dist, product)
            item.alternative = f"{alt[0].sku} {alt[0].label}" if alt else ""
        items.append(item)

    unavailable = [i for i in items if i.available <= 0]
    due = [i for i in items if i.available > 0 and i.status != "not_due"]
    not_due = [i for i in items if i.available > 0 and i.status == "not_due"]
    due.sort(key=lambda i: (i.status != "overdue", -i.value))
    not_due.sort(key=lambda i: -i.days_since / max(prefs.frequency_days, 1))

    lead: list[DueItem] = []
    running = 0
    for item in due:
        if len(lead) >= MAX_LEAD or (lead and running >= prefs.typical_order_value):
            break
        lead.append(item)
        running += item.value
    lead_skus = {i.sku for i in lead}
    others = [i for i in due if i.sku not in lead_skus]

    opted = prefs.do_not_call or retailer.id in opted_out
    overdue_share = (sum(1 for i in due if i.status == "overdue") / len(due)) if due else 0
    expected = sum(i.value for i in lead)
    priority = round(expected * (1 + 0.5 * overdue_share))

    next_due = None
    if not due and not_due:
        next_due = min(max(0, round(prefs.frequency_days * DUE_SOON) - i.days_since) for i in not_due)

    if opted:
        should_call, reason = False, "Do-not-call: retailer has opted out"
    elif not due:
        should_call = False
        reason = "Nothing due that is in stock" + (f" (next item due in ~{next_due} days)" if next_due is not None else "")
    else:
        should_call, reason = True, ""

    address_as = None
    first = retailer.name.split()[0] if retailer.name else ""
    if first in SURNAMES and retailer.language in HONORIFIC_LANGUAGES:
        address_as = f"{first} ji"

    brief = Brief(
        retailer=retailer, window=prefs.window, cycle_days=prefs.frequency_days,
        typical_order_value=prefs.typical_order_value, preferred_brands=prefs.brands, do_not_call=opted,
        should_call=should_call, skip_reason=reason, priority=priority if should_call else 0,
        expected_value=expected, address_as=address_as, lead=lead, others=others, not_due=not_due,
        unavailable=unavailable, next_due_in_days=next_due,
    )
    brief.cross_sell = pick_cross_sell(dataset, ledger, retailer, brief)
    return brief


def _trial_qty(product: Product, free: int) -> int:
    qty = max(2, min(12, round(TRIAL_VALUE_INR / product.price)))
    return max(1, min(qty, free))


def _mentioned(text: str) -> tuple[set[str], set[tuple[int, str]], set[str]]:
    lowered = text.lower()
    brands = {b for b in BRAND_NAMES if b.lower() in lowered}
    sizes = set()
    for match in PACK_PATTERN.finditer(text):
        factor, measure = PACK_FACTORS[match.group(2).lower()]
        sizes.add((round(float(match.group(1)) * factor), measure))
    keywords = {w for w in ("oats", "gold", "active", "serum", "gel", "deodorant", "honey", "peanut") if w in lowered}
    return brands, sizes, keywords


def pick_cross_sell(dataset: Dataset, ledger: StockLedger, retailer: Retailer, brief: Brief) -> CrossSell | None:
    hint = dataset.prefs(retailer.id).cross_sell
    lowered = hint.lower()
    dist = retailer.distributor_id
    bought = {line.sku: line for line in dataset.purchases(retailer.id)}
    taken = {i.sku for i in brief.lead}

    def free(p: Product) -> int:
        return ledger.available(dist, p.sku)

    brands, sizes, keywords = _mentioned(hint)

    if "trade" in lowered and len(sizes) >= 2:
        small_size, big_size = sorted(sizes)[0], sorted(sizes)[-1]
        for product in dataset.products.values():
            family = dataset.family(product)
            sized = {(p.pack_amount, p.pack_measure): p for p in family}
            if product.brand in brands and small_size in sized and big_size in sized and product is sized[small_size]:
                small, big = sized[small_size], sized[big_size]
                if small.sku in bought and free(big) > 0 and big.sku not in taken:
                    base = bought[small.sku].last_qty
                    qty = max(2, min(free(big), round(base * small.pack_amount / big.pack_amount)))
                    small_rate = small.mrp / small.pack_amount * 100
                    big_rate = big.mrp / big.pack_amount * 100
                    pitch = (f"They buy {small.label}. Suggest adding {big.pack_size} too: shoppers pay "
                             f"Rs {big_rate:.0f} per 100 {big.pack_measure} instead of Rs {small_rate:.0f}, and the "
                             f"shop earns Rs {big.mrp - big.price} a unit instead of Rs {small.mrp - small.price}.")
                    return CrossSell(big.sku, big.label, "trade_up", pitch, qty, big.price, big.mrp)

    if "trade" in lowered and sizes:
        pool = [p for p in dataset.products.values()
                if p.brand in brands and (p.pack_amount, p.pack_measure) in sizes and "coconut oil" in p.name.lower()]
        fresh = sorted((p for p in pool if p.sku not in bought and free(p) > 0 and p.sku not in taken),
                       key=lambda p: -p.pack_amount)
        if not fresh:
            return None
        product = fresh[0]
        qty = _trial_qty(product, free(product))
        pitch = (f"They don't stock {product.label} yet. Suggest a small trial of {qty} "
                 f"(about Rs {qty * product.price}). MRP Rs {product.mrp}, shop margin Rs {product.mrp - product.price} a unit.")
        return CrossSell(product.sku, product.label, "new_line", pitch, qty, product.price, product.mrp)

    pool = [p for p in dataset.products.values() if p.brand in brands]
    if keywords:
        pool = [p for p in pool if any(k in p.name.lower() for k in keywords)] or pool
    kind_new = "new_line"
    if not pool or "no clear" in lowered:
        missing = [b for b in brief.preferred_brands if b not in {dataset.product(s).brand for s in bought}]
        pool = [p for p in dataset.products.values() if p.brand in missing]
        kind_new = "whitespace"
    pool = [p for p in pool if free(p) > 0 and p.sku not in taken]
    if not pool:
        return None

    if "replenish" in lowered:
        owned = [p for p in pool if p.sku in bought]
        if owned:
            product = max(owned, key=lambda p: bought[p.sku].days_since / max(brief.cycle_days, 1))
            qty, _ = suggest_quantity(bought[product.sku].last_qty, bought[product.sku].previous_qty)
            qty = min(qty, free(product))
            pitch = (f"{product.label} is a regular for them (last order {bought[product.sku].last_qty}, "
                     f"{bought[product.sku].days_since} days ago). Ask if they need it this time.")
            return CrossSell(product.sku, product.label, "replenish", pitch, qty, product.price, product.mrp)

    fresh = [p for p in pool if p.sku not in bought]
    if not fresh:
        return None
    preferred = set(brief.preferred_brands)
    product = sorted(fresh, key=lambda p: (p.brand not in preferred, p.price, p.sku))[0]
    qty = _trial_qty(product, free(product))
    reason = f"{product.brand} is one of their preferred brands, but they" if kind_new == "whitespace" else "They"
    pitch = (f"{reason} don't stock {product.label} yet. Suggest a small trial of {qty} "
             f"(about Rs {qty * product.price}). MRP Rs {product.mrp}, shop margin Rs {product.mrp - product.price} a unit.")
    return CrossSell(product.sku, product.label, kind_new, pitch, qty, product.price, product.mrp)


def plan_day(dataset: Dataset, ledger: StockLedger, today: date | None = None,
             opted_out: set[str] | frozenset = frozenset()) -> tuple[list[Brief], list[Brief]]:
    briefs = [build_brief(dataset, ledger, rid, today, opted_out) for rid in sorted(dataset.retailers)]
    calls = [b for b in briefs if b.should_call]
    skipped = [b for b in briefs if not b.should_call]
    calls.sort(key=lambda b: (b.window, -b.priority))
    return calls, skipped
