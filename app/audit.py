from __future__ import annotations

import re
import sys
from collections import Counter

from .data import Dataset


def _readme_counts(dataset: Dataset) -> dict[str, int]:
    text = dataset.readme.get("Records", "")
    labels = {
        "retailers": r"([\d,]+)\s+retailers",
        "skus": r"([\d,]+)\s+SKUs",
        "purchase rows": r"([\d,]+)\s+purchase-history rows",
        "inventory rows": r"([\d,]+)\s+distributor-SKU inventory rows",
        "preference rows": r"([\d,]+)\s+preference rows",
        "scenarios": r"([\d,]+)\s+test scenarios",
    }
    found = {}
    for label, pattern in labels.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            found[label] = int(match.group(1).replace(",", ""))
    return found


def findings(dataset: Dataset) -> list[dict]:
    out: list[dict] = []

    dnc = [r for r in dataset.retailers.values() if dataset.prefs(r.id).do_not_call]
    if dnc:
        names = ", ".join(f"{r.id} {r.name}" for r in dnc)
        out.append({
            "title": f"{len(dnc)} retailers are flagged do-not-call",
            "detail": f"{names}. The planner never queues them and the API refuses to start a call.",
            "impact": "compliance",
        })

    sharma = dataset.retailers.get("R001")
    if sharma and dataset.prefs("R001").do_not_call:
        p500 = dataset.purchase("R001", "P001")
        p200 = dataset.purchase("R001", "P002")
        gold = [line for line in dataset.purchases("R001") if dataset.product(line.sku).name == "Saffola Gold"]
        bits = [f"{sharma.name} (R001), the shop in the sample dialogue, is on the do-not-call list"]
        bits.append("has never bought Parachute 500 ml" if not p500 else f"last bought {p500.last_qty} x 500 ml")
        if p200:
            bits.append(f"last bought {p200.last_qty} x Parachute 200 ml, not 8")
        if "Parachute" not in dataset.prefs("R001").brands:
            bits.append("does not list Parachute as a preferred brand")
        if gold:
            g = dataset.product(gold[0].sku)
            bits.append(f"buys Saffola Gold as {g.pack_size} packs, so '10 litre' could mean 2 x {g.pack_size} "
                        f"(Rs {2 * g.price}) or 10 x 1 litre (Rs {10 * dataset.product('P006').price})")
        out.append({
            "title": "The sample call in the brief does not match the data",
            "detail": "; ".join(bits) + ".",
            "impact": "the agent must work from data, not from the script",
        })

    names = Counter(r.name for r in dataset.retailers.values())
    repeated = {n: c for n, c in names.items() if c > 1}
    if repeated:
        out.append({
            "title": f"{len(repeated)} shop names are shared by {sum(repeated.values())} retailers",
            "detail": "For example " + ", ".join(f"{n} (x{c})" for n, c in list(repeated.items())[:4])
                      + ". Calls are keyed on Retailer_ID and the contact ID, never on the name.",
            "impact": "identification",
        })

    claimed = _readme_counts(dataset)
    actual = {
        "retailers": len(dataset.retailers), "skus": len(dataset.products), "purchase rows": dataset.purchase_rows,
        "inventory rows": len(dataset.stock), "preference rows": len(dataset.preferences),
        "scenarios": len(dataset.scenarios),
    }
    wrong = [f"{label}: README says {claimed[label]}, workbook has {actual[label]}"
             for label in claimed if claimed[label] != actual[label]]
    if wrong:
        out.append({"title": "README record counts are off", "detail": "; ".join(wrong) + ".",
                    "impact": "data hygiene"})

    tight, out_of_stock = 0, 0
    for retailer in dataset.retailers.values():
        for line in dataset.purchases(retailer.id):
            row = dataset.stock_row(retailer.distributor_id, line.sku)
            free = row.available if row else 0
            out_of_stock += free == 0
            tight += line.avg_monthly_qty > free
    out.append({
        "title": f"Stock is contested: {tight} of {dataset.purchase_rows} buying lines need more than the distributor holds",
        "detail": f"{out_of_stock} lines are for SKUs that are out of stock at the retailer's distributor. Each distributor "
                  f"serves {round(len(dataset.retailers) / max(1, len({r.distributor_id for r in dataset.retailers.values()})))} "
                  f"retailers, so the agent holds stock per call to avoid selling the same units twice.",
        "impact": "inventory",
    })

    whitespace = 0
    for retailer in dataset.retailers.values():
        bought = {dataset.product(l.sku).brand for l in dataset.purchases(retailer.id)}
        if set(dataset.prefs(retailer.id).brands) - bought:
            whitespace += 1
    if whitespace:
        out.append({
            "title": f"{whitespace} retailers list preferred brands they have never bought",
            "detail": "Used as a cross-sell source when the Cross_Sell_Opportunity column says there is no clear opportunity.",
            "impact": "cross-sell",
        })

    out.append({
        "title": "No box or case conversion exists",
        "detail": "Product_Master has selling units and MOQ but no pieces per box, so any 'boxes' request is clarified "
                  "into pieces instead of guessed.",
        "impact": "order accuracy",
    })

    windows = Counter(dataset.prefs(r).window for r in dataset.retailers)
    share = ", ".join(f"{w} {c * 100 // len(dataset.retailers)}%" for w, c in sorted(windows.items()))
    out.append({
        "title": f"History and stock are a snapshot as of {dataset.as_of:%d %b %Y}",
        "detail": f"Every Order_Date + Days_Since_Last_Order lands on {dataset.as_of.isoformat()}, which the planner "
                  f"treats as today. Preferred calling windows: {share}.",
        "impact": "planning",
    })
    return out


def main(path: str) -> None:
    dataset = Dataset.load(path)
    for item in findings(dataset):
        print(f"- {item['title']}\n  {item['detail']}\n")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/Marico_Synthetic_Data_Final_Excel_Data.xlsx")
