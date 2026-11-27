from app.catalog import search
from app.inventory import StockLedger
from app.planner import build_brief, plan_day, suggest_quantity


def skus(result):
    return [p.sku for p in result.matches]


def test_small_bottle_is_ambiguous(dataset):
    result = search(dataset, "Parachute small bottle")
    assert result.ambiguous
    assert {"P002", "P004", "P005"} <= set(skus(result))


def test_exact_pack_resolves(dataset):
    assert skus(search(dataset, "Parachute 500 ml")) == ["P001"]
    assert skus(search(dataset, "पैराशूट 500 ml")) == ["P001"]
    assert skus(search(dataset, "Saffola Gold 5 litre")) == ["P007"]
    assert skus(search(dataset, "masala oats")) == ["P011"]
    assert skus(search(dataset, "P013")) == ["P013"]


def test_total_volume_is_not_a_pack(dataset):
    result = search(dataset, "Saffola Gold 10 litre")
    assert set(skus(result)) == {"P006", "P007"}
    assert result.ambiguous and "total quantity" in result.note


def test_unknown_product(dataset):
    assert skus(search(dataset, "Dettol soap")) == []


def test_suggestion_follows_trend():
    assert suggest_quantity(11, 10) == (11, "steady: last 11, before that 10")
    assert suggest_quantity(9, 10) == (10, "steady: last 9, before that 10")
    assert suggest_quantity(12, 10)[0] == 12
    assert suggest_quantity(15, 10)[0] == 15
    assert suggest_quantity(6, 10)[0] == 6


def test_do_not_call_is_never_planned(dataset):
    calls, skipped = plan_day(dataset, StockLedger(dataset))
    planned = {b.retailer.id for b in calls}
    assert not planned & {"R001", "R025", "R082"}
    assert {"R001", "R025", "R082"} <= {b.retailer.id for b in skipped}


def test_recorded_opt_out_is_respected(dataset):
    calls, _ = plan_day(dataset, StockLedger(dataset), opted_out={"R003"})
    assert "R003" not in {b.retailer.id for b in calls}


def test_plan_is_ordered_by_window_then_priority(dataset):
    calls, _ = plan_day(dataset, StockLedger(dataset))
    keys = [(b.window, -b.priority) for b in calls]
    assert keys == sorted(keys)


def test_brief_never_offers_out_of_stock(dataset):
    ledger = StockLedger(dataset)
    for retailer_id in dataset.retailers:
        brief = build_brief(dataset, ledger, retailer_id)
        offered = brief.lead + brief.others
        assert all(i.available > 0 for i in offered)
        assert all(i.suggested_qty <= i.available for i in offered)
        if brief.cross_sell:
            assert ledger.available(brief.retailer.distributor_id, brief.cross_sell.sku) >= brief.cross_sell.suggested_qty
            assert brief.cross_sell.sku not in {i.sku for i in brief.lead}


def test_lead_items_stop_near_typical_order_value(dataset):
    ledger = StockLedger(dataset)
    brief = build_brief(dataset, ledger, "R003")
    values = [i.value for i in brief.lead]
    assert 1 <= len(values) <= 4
    assert sum(values[:-1]) < brief.typical_order_value


def test_out_of_stock_items_carry_an_alternative(dataset):
    brief = build_brief(dataset, StockLedger(dataset), "R043")
    gone = {i.sku: i for i in brief.unavailable}
    assert {"P006", "P007"} <= set(gone)
    assert gone["P006"].alternative


def test_trade_up_cross_sell(dataset):
    ledger = StockLedger(dataset)
    kinds = {}
    for retailer_id in dataset.retailers:
        brief = build_brief(dataset, ledger, retailer_id)
        if brief.cross_sell:
            kinds.setdefault(brief.cross_sell.kind, brief)
    assert {"trade_up", "replenish", "new_line", "whitespace"} <= set(kinds)
    assert kinds["trade_up"].cross_sell.sku == "P001"


def test_honorific_only_for_surname_shops(dataset):
    ledger = StockLedger(dataset)
    assert build_brief(dataset, ledger, "R059").address_as == "Sharma ji"
    assert build_brief(dataset, ledger, "R003").address_as is None


def test_planner_uses_data_date(dataset):
    assert dataset.as_of.isoformat() == "2026-11-20"
