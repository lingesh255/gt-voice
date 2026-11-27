from __future__ import annotations

import threading
from dataclasses import dataclass, field

import httpx

from .outcomes import OutcomeStore, now_iso

REQUIRED = ("order_id", "retailer_id", "distributor_id", "lines", "order_value_inr", "confirmation")


@dataclass
class Delivery:
    order_id: str
    key: str
    payload: dict
    attempts: int = 0
    status: str = "pending"
    detail: str = ""


class OrderGateway:
    def __init__(self, store: OutcomeStore, webhook_url: str = "", timeout: float = 5.0):
        self.store = store
        self.webhook_url = webhook_url.strip()
        self.timeout = timeout
        self.outbox: list[Delivery] = []
        self._lock = threading.Lock()

    def record(self, payload: dict) -> dict:
        saved = self.store.add("orders", payload)
        if self.webhook_url:
            with self._lock:
                self.outbox.append(Delivery(payload["order_id"], payload["idempotency_key"], payload))
        return saved

    async def deliver_pending(self) -> list[Delivery]:
        if not self.webhook_url:
            return []
        with self._lock:
            pending = [d for d in self.outbox if d.status in ("pending", "failed") and d.attempts < 3]
        if not pending:
            return []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for delivery in pending:
                delivery.attempts += 1
                try:
                    response = await client.post(
                        self.webhook_url, json=delivery.payload,
                        headers={"Idempotency-Key": delivery.key},
                    )
                    if response.status_code < 300:
                        delivery.status, delivery.detail = "delivered", response.text[:200]
                    else:
                        delivery.status, delivery.detail = "failed", f"HTTP {response.status_code}"
                except httpx.HTTPError as exc:
                    delivery.status, delivery.detail = "failed", str(exc)[:200]
        return pending


@dataclass
class MockDistributorOMS:
    received: list[dict] = field(default_factory=list)
    by_key: dict[str, dict] = field(default_factory=dict)

    def accept(self, payload: dict, key: str) -> tuple[int, dict]:
        missing = [name for name in REQUIRED if name not in payload]
        if missing:
            return 422, {"status": "rejected", "error": f"missing fields: {', '.join(missing)}"}
        if payload.get("confirmation", {}).get("status") != "confirmed":
            return 422, {"status": "rejected", "error": "order is not confirmed by the retailer"}
        if not payload["lines"]:
            return 422, {"status": "rejected", "error": "order has no lines"}
        total = sum(line["quantity"] * line["unit_price_inr"] for line in payload["lines"])
        if total != payload["order_value_inr"]:
            return 422, {"status": "rejected", "error": "order value does not match its lines"}
        key = key or payload.get("idempotency_key") or payload["order_id"]
        if key in self.by_key:
            return 200, {**self.by_key[key], "duplicate": True}
        reply = {
            "status": "accepted",
            "dms_reference": f"DMS-{payload['distributor_id']}-{len(self.received) + 1:05d}",
            "order_id": payload["order_id"],
            "received_at": now_iso(),
        }
        self.by_key[key] = reply
        self.received.append({**payload, "dms_reference": reply["dms_reference"]})
        return 201, reply
