from __future__ import annotations

UNITS = ["piece", "dozen", "litre", "ml", "kg", "g", "box"]

SOURCES = ["retailer_request", "suggested", "cross_sell", "alternative"]

OUTCOMES = ["order_placed", "no_order", "callback_scheduled", "escalated", "wrong_person", "do_not_call"]

ESCALATIONS = ["human_request", "pricing_or_discount", "payment_or_credit", "product_quality", "delivery", "other"]


def _line_args(required: list[str], extra: dict | None = None) -> dict:
    properties = {
            "sku": {"type": "string", "description": "SKU id, e.g. P001"},
            "quantity": {"type": "number", "description": "Number the retailer asked for, in the unit below"},
            "unit": {"type": "string", "enum": UNITS,
                     "description": "Unit the retailer used. 'piece' for pieces/packs/bottles."},
            "retailer_said": {"type": "string",
                              "description": "The retailer's exact words for this item, copied from their message"},
            "verified": {"type": "boolean",
                         "description": "Only true after the engine asked you to double-check and the retailer "
                                        "then confirmed the exact piece count in their own words"},
    }
    properties.update(extra or {})
    return {"type": "object", "properties": properties, "required": required}


TOOLS = [
    {
        "name": "confirm_identity",
        "description": "Record whether the person who answered is from the shop you are calling (owner or staff). "
                       "Call it right after they answer your identity question. Until it is true, do not mention "
                       "any purchase history or order details.",
        "parameters": {
            "type": "object",
            "properties": {
                "is_right_shop": {"type": "boolean", "description": "true if they confirmed this is the shop"},
                "note": {"type": "string", "description": "Who answered, if they said"},
            },
            "required": ["is_right_shop"],
        },
    },
    {
        "name": "get_call_brief",
        "description": "Get this retailer's brief: items due for reorder with last and suggested quantities, items "
                       "that are out of stock today with alternatives, and one cross-sell idea. Works only after "
                       "confirm_identity(true).",
    },
    {
        "name": "propose_basket",
        "description": "Put the retailer's usual order (the brief's lead_with items at their suggested quantities, "
                       "capped by today's stock) into the order in one step, ready to offer as a single question. "
                       "Use it right after get_call_brief unless the retailer has already started listing items; "
                       "items they already ordered are kept as they said them.",
    },
    {
        "name": "find_products",
        "description": "Search the catalogue by what the retailer said, e.g. 'Parachute 500 ml' or 'Saffola Gold'. "
                       "Returns matching SKUs with price, MRP and today's stock. If ambiguous is true, ask the "
                       "retailer which one before adding anything.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Product as described, in English"}},
            "required": ["query"],
        },
    },
    {
        "name": "check_stock",
        "description": "Units available today at this retailer's distributor for one SKU.",
        "parameters": {
            "type": "object",
            "properties": {"sku": {"type": "string"}},
            "required": ["sku"],
        },
    },
    {
        "name": "add_to_order",
        "description": "Add a new line to the order. The engine checks stock, units and the retailer's words, and "
                       "may refuse with a reason you must act on.",
        "parameters": _line_args(
            ["sku", "quantity", "unit", "retailer_said", "source"],
            {"source": {"type": "string", "enum": SOURCES,
                        "description": "Who brought this item up: retailer_request, suggested (from the brief), "
                                       "cross_sell, or alternative (replacing an out-of-stock item)"}},
        ),
    },
    {
        "name": "change_quantity",
        "description": "Set a new total quantity for a line already in the order (for example 10 changed to 15).",
        "parameters": _line_args(["sku", "quantity", "unit", "retailer_said"]),
    },
    {
        "name": "remove_from_order",
        "description": "Remove a line the retailer no longer wants.",
        "parameters": {
            "type": "object",
            "properties": {"sku": {"type": "string"}},
            "required": ["sku"],
        },
    },
    {
        "name": "read_back_order",
        "description": "Get the final order summary to read back. Call it when the retailer has nothing more to add. "
                       "Any change after this needs a fresh read-back.",
    },
    {
        "name": "confirm_order",
        "description": "Place the order after you read it back and the retailer clearly said yes. Pass their exact "
                       "words; the engine refuses anything that is not a clear yes.",
        "parameters": {
            "type": "object",
            "properties": {"retailer_said": {"type": "string", "description": "Retailer's exact confirmation words"}},
            "required": ["retailer_said"],
        },
    },
    {
        "name": "schedule_callback",
        "description": "Create a callback when the retailer is busy or asks to be called later.",
        "parameters": {
            "type": "object",
            "properties": {
                "when": {"type": "string", "description": "When they asked, in their words, e.g. 'after 5 PM'"},
                "day": {"type": "string", "enum": ["today", "tomorrow", "later"]},
                "time_24h": {"type": "string", "description": "HH:MM if a time was given, e.g. 17:00"},
                "reason": {"type": "string"},
            },
            "required": ["when", "reason"],
        },
    },
    {
        "name": "log_complaint",
        "description": "Record a complaint about a previous order or delivery (wrong quantity, damaged goods, "
                       "missing items). Creates a ticket for the sales representative and distributor.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "What went wrong, in English"},
                "sku": {"type": "string", "description": "SKU involved, if known"},
            },
            "required": ["summary"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": "Hand over to a human sales representative: when the retailer asks for a person, wants a "
                       "discount or scheme, raises payment or credit issues, or anything you cannot handle.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ESCALATIONS},
                "summary": {"type": "string", "description": "One or two lines for the representative"},
            },
            "required": ["category", "summary"],
        },
    },
    {
        "name": "record_do_not_call",
        "description": "The retailer asked not to be called again. Records the opt-out permanently.",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
    {
        "name": "log_market_insight",
        "description": "Note useful market information the retailer shares, such as a competitor promotion, "
                       "price change or demand trend.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "competitor": {"type": "string"},
                "brand": {"type": "string", "description": "Our brand affected, if any"},
            },
            "required": ["summary"],
        },
    },
    {
        "name": "end_call",
        "description": "Close the call record once the conversation is finished, then say a short goodbye.",
        "parameters": {
            "type": "object",
            "properties": {
                "outcome": {"type": "string", "enum": OUTCOMES},
                "note": {"type": "string"},
            },
            "required": ["outcome"],
        },
    },
]

TOOL_NAMES = {tool["name"] for tool in TOOLS}
