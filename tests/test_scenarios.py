import pytest

from app.oms import MockDistributorOMS
from app.order import CONFIRMED
from app.planner import plan_day
from app.session import CallBlocked, CallSession
from app.tools import TOOLS
from tests.conftest import add, change, place


def test_cs01_interested_retailer(call, ctx):
    s = call("R003")
    before = ctx.ledger.available("D001", "P001")
    assert add(s, "P001", 15, "Parachute 500 ml ka stock kam hai, 15 bhej do")["ok"]
    result = place(s)
    assert result["ok"]
    order = ctx.store.list("orders")[0]
    assert order["retailer_id"] == "R003" and order["distributor_id"] == "D001"
    assert order["lines"][0]["sku"] == "P001" and order["lines"][0]["quantity"] == 15
    assert order["order_value_inr"] == 15 * 176
    assert order["confirmation"]["status"] == "confirmed"
    assert ctx.ledger.available("D001", "P001") == before - 15


def test_cs02_busy_retailer(call, ctx):
    s = call("R003")
    result = s.call_tool("schedule_callback", {"when": "after 5 PM", "day": "today", "time_24h": "17:00",
                                               "reason": "busy at the counter"})
    assert result["ok"]
    s.call_tool("end_call", {"outcome": "callback_scheduled"})
    tasks = ctx.store.list("tasks")
    assert tasks[0]["kind"] == "callback"
    assert "T17:00" in tasks[0]["due_at"]
    assert not ctx.store.list("orders")
    assert s.outcome == "callback_scheduled"


def test_cs02_rejects_bad_time(call):
    s = call("R003")
    assert not s.call_tool("schedule_callback", {"when": "evening", "reason": "busy", "time_24h": "5pm"})["ok"]


def test_cs03_out_of_stock_sku(call, ctx):
    s = call("R043")
    result = add(s, "P006", 10, "Saffola Gold 1 litre ke 10")
    assert not result["ok"] and "out of stock" in result["error"]
    assert result["alternatives"]
    assert all(alt["available_units"] > 0 for alt in result["alternatives"])
    assert "P006" not in s.order.lines
    alternative = result["alternatives"][0]["sku"]
    assert add(s, alternative, 2, "theek hai, 2 de do", source="alternative")["ok"]
    place(s)
    assert {line["sku"] for line in ctx.store.list("orders")[0]["lines"]} == {alternative}


def test_cs04_quantity_changed(call, ctx):
    s = call("R003")
    assert add(s, "P001", 10, "500 ml ke 10")["ok"]
    assert change(s, "P001", 15, "nahi, 10 nahi 15 kar do")["ok"]
    place(s)
    assert ctx.store.list("orders")[0]["lines"][0]["quantity"] == 15


def test_cs04_add_twice_is_refused(call):
    s = call("R003")
    add(s, "P001", 10, "500 ml ke 10")
    assert not add(s, "P001", 5, "aur 5")["ok"]


def test_cs05_language_switch_is_tracked(call):
    s = call("R003")
    s.add_turn("agent", "जी, 500 ml के 15 pieces?", "hi-IN")
    s.add_turn("agent", "Sure, 15 pieces of Parachute 500 ml.", "en-IN")
    assert s.language == "en-IN" and {"hi-IN", "en-IN"} <= s.languages_used


def test_cs06_previous_order_complaint(call, ctx):
    s = call("R003")
    result = s.call_tool("log_complaint", {"summary": "Last Parachute 500 ml order came short by 2", "sku": "P001"})
    assert result["ok"] and result["ticket_id"].startswith("CP-")
    ticket = ctx.store.list("tasks")[0]
    history = ctx.dataset.purchase("R003", "P001")
    assert ticket["related"]["transaction_id"] == history.transaction_id
    assert ticket["related"]["billed_quantity"] == history.last_qty
    added = add(s, "P014", 5, "gel 5")
    assert "suggest" not in added["next"].lower()


@pytest.mark.parametrize("unit,said", [("box", "2 box bhej do"), ("piece", "2 box bhej do"),
                                       ("piece", "10 boxes of parachute")])
def test_cs07_box_versus_piece(call, unit, said):
    s = call("R003")
    quantity = 10 if "10" in said else 2
    result = add(s, "P001", quantity, said, unit=unit)
    assert not result["ok"] and "pieces" in result["error"]
    assert "P001" not in s.order.lines


def test_cs07_after_clarifying_pieces(call):
    s = call("R003")
    assert add(s, "P001", 24, "2 box matlab 24 pieces", verified=True)["ok"]


def test_cs08_no_discount_path(call, ctx):
    names = {tool["name"] for tool in TOOLS}
    assert not any("discount" in name for name in names)
    for tool in TOOLS:
        assert "price" not in tool.get("parameters", {}).get("properties", {})
    s = call("R003")
    add(s, "P001", 10, "10 bhejo")
    s.call_tool("escalate_to_human", {"category": "pricing_or_discount", "summary": "Wants 5% extra off"})
    place(s)
    order = ctx.store.list("orders")[0]
    assert order["lines"][0]["unit_price_inr"] == 176 and order["order_value_inr"] == 1760
    assert ctx.store.list("tasks")[0]["category"] == "pricing_or_discount"


def test_cs09_not_interested(call, ctx):
    s = call("R003")
    s.call_tool("end_call", {"outcome": "order_placed"})
    assert s.outcome == "no_order"
    assert not ctx.store.list("orders")
    assert ctx.store.list("calls")[0]["outcome"] == "no_order"


def test_cs10_human_request(call, ctx):
    s = call("R003")
    result = s.call_tool("escalate_to_human", {"category": "human_request", "summary": "Wants to talk to Ramesh"})
    assert result["ok"]
    assert ctx.store.list("tasks")[0]["kind"] == "escalation"


def test_cs11_ambiguous_quantity(call):
    s = call("R003")
    result = add(s, "P001", 8, "send a few more than last time")
    assert not result["ok"] and "number" in result["error"]
    assert not s.order.lines


def test_cs12_sku_ambiguity(call):
    s = call("R003")
    result = s.call_tool("find_products", {"query": "Parachute small bottle"})
    assert result["ambiguous"] and len(result["matches"]) > 1


def test_cs13_stock_constraint(call):
    s = call("R003")
    available = s.call_tool("check_stock", {"sku": "P006"})["available_units"]
    result = add(s, "P006", available + 7, f"Saffola Gold 1 litre {available + 7}")
    assert not result["ok"] and result["available_units"] == available
    assert add(s, "P006", available, f"theek hai {available} hi bhejo")["ok"]


def test_cs14_cross_sell_once(call):
    s = call("R003")
    cross = s.brief.cross_sell
    assert cross
    first = add(s, "P001", 10, "10 bhejo")
    assert cross.product in first["next"]
    second = add(s, "P014", 5, "5 bhejo")
    assert cross.product not in second["next"]
    assert add(s, cross.sku, cross.suggested_qty, "theek hai, try kar lete hain", source="cross_sell")["ok"]


def test_cs15_wrong_retailer(call):
    s = call("R003", confirmed=False)
    s.call_tool("confirm_identity", {"is_right_shop": False})
    assert not s.call_tool("get_call_brief", {})["ok"]
    found = s.call_tool("find_products", {"query": "Parachute 500 ml"})
    assert "retailer_last_qty" not in found["matches"][0]
    assert not add(s, "P001", 5, "5")["ok"]
    s.call_tool("end_call", {"outcome": "wrong_person"})
    assert s.outcome == "wrong_person" and not s.brief_shared


def test_cs15_history_hidden_before_identity(call):
    s = call("R003", confirmed=False)
    assert not s.call_tool("get_call_brief", {})["ok"]
    assert "retailer_last_qty" not in s.call_tool("find_products", {"query": "Parachute 500 ml"})["matches"][0]


def test_cs16_do_not_call(call, ctx):
    s = call("R003")
    s.call_tool("record_do_not_call", {"reason": "asked not to be called"})
    s.call_tool("end_call", {"outcome": "do_not_call"})
    assert "R003" in ctx.store.opted_out()
    with pytest.raises(CallBlocked):
        CallSession(ctx, "R003")
    calls, _ = plan_day(ctx.dataset, ctx.ledger, opted_out=ctx.store.opted_out())
    assert "R003" not in {b.retailer.id for b in calls}


def test_cs16_flagged_retailers_cannot_be_called(ctx):
    for retailer_id in ("R001", "R025", "R082"):
        with pytest.raises(CallBlocked):
            CallSession(ctx, retailer_id)


def test_cs17_summary_correction(call, ctx):
    s = call("R003")
    add(s, "P001", 15, "15")
    add(s, "P014", 10, "gel 10")
    assert s.call_tool("read_back_order", {})["ok"]
    assert change(s, "P014", 8, "gel 10 nahi, 8")["ok"]
    assert not s.call_tool("confirm_order", {"retailer_said": "haan"})["ok"]
    place(s, "haan ab sahi hai")
    lines = {l["sku"]: l["quantity"] for l in ctx.store.list("orders")[0]["lines"]}
    assert lines == {"P001": 15, "P014": 8}


def test_cs18_unclear_speech(call):
    s = call("R003")
    result = add(s, "P001", 15, "[unclear] parachute ... pan...", verified=True)
    assert not result["ok"] and "repeat" in result["error"]


def test_cs19_demand_signal(call, ctx):
    s = call("R003")
    s.call_tool("log_market_insight", {"summary": "Competitor coconut oil running buy 2 get 1", "competitor": "local brand",
                                       "brand": "Parachute"})
    s.call_tool("end_call", {"outcome": "no_order"})
    assert ctx.store.list("insights")[0]["city"] == "Delhi"
    assert not ctx.store.list("orders")


def test_surprise_refuses_then_agrees_to_callback(call, ctx):
    s = call("R003")
    s.call_tool("schedule_callback", {"when": "next week", "day": "later", "reason": "not ordering today"})
    s.call_tool("end_call", {"outcome": "callback_scheduled"})
    assert ctx.store.list("tasks")[0]["kind"] == "callback" and not ctx.store.list("orders")


def test_confirmation_needs_a_clear_yes(call, ctx):
    s = call("R003")
    add(s, "P001", 5, "5")
    s.call_tool("read_back_order", {})
    assert not s.call_tool("confirm_order", {"retailer_said": "ruko, ek minute"})["ok"]
    assert s.order.state != CONFIRMED
    assert s.call_tool("confirm_order", {"retailer_said": "haan"})["ok"]


def test_mismatched_number_is_questioned(call):
    s = call("R003")
    result = add(s, "P001", 50, "500 ml ke 15")
    assert not result["ok"] and "15" in result["error"]


def test_unusually_large_quantity(call):
    s = call("R003")
    result = add(s, "P001", 90, "90 pieces")
    assert not result["ok"] and "usually" in result["error"]
    assert add(s, "P001", 90, "haan 90 hi", verified=True)["ok"]


def test_litres_get_a_pack_tip(call):
    s = call("R003")
    assert s.call_tool("check_stock", {"sku": "P007"})["available_units"] >= 2
    result = add(s, "P006", 10, "Saffola Gold 10 litre bhej dena", unit="litre")
    assert result["ok"]
    assert "2 x Saffola Gold 5 litre for Rs 1640 instead of Rs 1680" in result["pack_tip"]


def test_litres_on_the_wrong_pack_are_questioned(call):
    s = call("R003")
    result = add(s, "P007", 10, "Saffola Gold 10 litre bhej dena")
    assert not result["ok"] and "2 pieces" in result["error"]


def test_two_calls_cannot_sell_the_same_units(ctx):
    first = CallSession(ctx, "R003")
    second = CallSession(ctx, "R005")
    for s in (first, second):
        s.call_tool("confirm_identity", {"is_right_shop": True})
    stock = first.call_tool("check_stock", {"sku": "P006"})["available_units"]
    assert add(first, "P006", stock, f"{stock} bhejo", verified=True)["ok"]
    result = add(second, "P006", 1, "1 bhejo")
    assert not result["ok"]
    first.finish()
    assert add(second, "P006", 1, "1 bhejo")["ok"]


def test_order_payload_is_accepted_by_distributor_oms(call, ctx):
    s = call("R003")
    add(s, "P001", 12, "12")
    place(s)
    payload = ctx.store.list("orders")[0]
    oms = MockDistributorOMS()
    status, body = oms.accept(payload, payload["idempotency_key"])
    assert status == 201 and body["status"] == "accepted"
    status, again = oms.accept(payload, payload["idempotency_key"])
    assert status == 200 and again["duplicate"] and again["dms_reference"] == body["dms_reference"]
    broken = {**payload, "order_value_inr": payload["order_value_inr"] + 1}
    assert oms.accept(broken, "other")[0] == 422


def test_call_record_is_written_once(call, ctx):
    s = call("R003")
    s.call_tool("end_call", {"outcome": "no_order"})
    s.finish()
    assert len(ctx.store.list("calls")) == 1


def test_tools_after_end_are_refused(call):
    s = call("R003")
    s.call_tool("end_call", {"outcome": "no_order"})
    assert not add(s, "P001", 1, "1")["ok"]


def test_bad_arguments_do_not_crash(call):
    s = call("R003")
    assert not s.call_tool("add_to_order", {"sku": "P001"})["ok"]
    assert not s.call_tool("add_to_order", {"sku": "P999", "quantity": 1, "unit": "piece", "retailer_said": "1",
                                            "source": "retailer_request"})["ok"]
    assert not s.call_tool("nonsense", {})["ok"]
