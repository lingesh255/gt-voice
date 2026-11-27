from tests.conftest import add, change


def offer(session):
    assert session.call_tool("get_call_brief", {})["ok"]
    return session.call_tool("propose_basket", {})


def test_basket_is_the_brief_lead_capped_by_stock(call, ctx):
    s = call("R003")
    result = offer(s)
    assert result["ok"]
    expected = {i.sku: min(i.suggested_qty, i.available) for i in s.brief.lead}
    assert {sku: line.quantity for sku, line in s.order.lines.items()} == expected
    assert result["total_inr"] == s.order.total == sum(i.value for i in s.brief.lead)
    for sku, quantity in expected.items():
        assert ctx.ledger.available("D001", sku) == ctx.ledger.base("D001", sku) - quantity


def test_one_yes_books_the_basket(call, ctx):
    s = call("R003")
    offer(s)
    assert s.call_tool("confirm_order", {"retailer_said": "haan, yahi bhej do"})["ok"]
    s.call_tool("end_call", {"outcome": "order_placed"})
    assert ctx.store.list("calls")[0]["basket"] == {"outcome": "accepted", "offered_value_inr": s.basket["value"]}


def test_editing_the_basket_needs_a_fresh_read_back(call, ctx):
    s = call("R003")
    offer(s)
    first = next(iter(s.order.lines))
    assert change(s, first, 5, "usme 5 hi bhejo", verified=True)["ok"]
    assert not s.call_tool("confirm_order", {"retailer_said": "haan"})["ok"]
    s.call_tool("read_back_order", {})
    assert s.call_tool("confirm_order", {"retailer_said": "haan"})["ok"]
    assert s.basket_outcome() == "edited"


def test_cross_sell_on_top_of_basket(call):
    s = call("R003")
    offer(s)
    cross = s.brief.cross_sell
    assert add(s, cross.sku, cross.suggested_qty, "haan, try kar lete hain", source="cross_sell")["ok"]
    s.call_tool("read_back_order", {})
    assert s.call_tool("confirm_order", {"retailer_said": "haan theek hai"})["ok"]
    assert s.basket_outcome() == "accepted_plus"


def test_retailer_items_first_then_basket(call):
    s = call("R003")
    s.call_tool("get_call_brief", {})
    assert add(s, "P001", 15, "Parachute 500 ml ke 15")["ok"]
    result = s.call_tool("propose_basket", {})
    assert result["ok"] and s.order.lines["P001"].quantity == 15
    assert len(s.order.lines) == 1 + len([i for i in s.brief.lead if i.sku != "P001"])


def test_declined_basket(call, ctx):
    s = call("R003")
    offer(s)
    s.call_tool("end_call", {"outcome": "no_order"})
    assert s.basket_outcome() == "declined"
    assert all(ctx.ledger.available("D001", sku) == ctx.ledger.base("D001", sku) for sku in s.basket["lines"])


def test_basket_needs_identity_and_brief(call):
    s = call("R003", confirmed=False)
    assert not s.call_tool("propose_basket", {})["ok"]
    s.call_tool("confirm_identity", {"is_right_shop": True})
    assert not s.call_tool("propose_basket", {})["ok"]
    assert offer(s)["ok"]
    assert not s.call_tool("propose_basket", {})["ok"]


def test_basket_skips_what_another_call_already_holds(ctx):
    from app.session import CallSession
    first = CallSession(ctx, "R003")
    second = CallSession(ctx, "R003")
    for s in (first, second):
        s.call_tool("confirm_identity", {"is_right_shop": True})
    lead = first.brief.lead[0]
    stock = ctx.ledger.available("D001", lead.sku)
    assert add(first, lead.sku, stock, f"{stock} bhejo", verified=True)["ok"]
    result = offer(second)
    assert result["ok"] and lead.sku not in second.order.lines
    assert "left_out" in result
