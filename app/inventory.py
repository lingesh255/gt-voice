from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass

from .data import Dataset

HOLD_SECONDS = 15 * 60


@dataclass
class Hold:
    quantity: int
    expires_at: float


class StockLedger:
    def __init__(self, dataset: Dataset, hold_seconds: int = HOLD_SECONDS, clock=time.monotonic):
        self.dataset = dataset
        self.hold_seconds = hold_seconds
        self.clock = clock
        self._lock = threading.RLock()
        self._committed: dict[tuple[str, str], int] = defaultdict(int)
        self._holds: dict[tuple[str, str], dict[str, Hold]] = defaultdict(dict)

    def base(self, distributor_id: str, sku: str) -> int:
        row = self.dataset.stock_row(distributor_id, sku)
        return row.available if row else 0

    def available(self, distributor_id: str, sku: str, session_id: str | None = None) -> int:
        with self._lock:
            key = (distributor_id, sku)
            self._expire(key)
            held = sum(h.quantity for sid, h in self._holds[key].items() if sid != session_id)
            return max(0, self.base(distributor_id, sku) - self._committed[key] - held)

    def held_by(self, distributor_id: str, sku: str, session_id: str) -> int:
        with self._lock:
            hold = self._holds[(distributor_id, sku)].get(session_id)
            return hold.quantity if hold else 0

    def status(self, distributor_id: str, sku: str, session_id: str | None = None) -> str:
        units = self.available(distributor_id, sku, session_id)
        if units <= 0:
            return "Out of stock"
        if units <= self.dataset.low_stock_threshold:
            return "Low stock"
        return "Available"

    def hold(self, distributor_id: str, sku: str, session_id: str, quantity: int) -> tuple[bool, int]:
        with self._lock:
            free = self.available(distributor_id, sku, session_id)
            if quantity > free:
                return False, free
            self._holds[(distributor_id, sku)][session_id] = Hold(quantity, self.clock() + self.hold_seconds)
            return True, free - quantity

    def release(self, distributor_id: str, sku: str, session_id: str) -> None:
        with self._lock:
            self._holds[(distributor_id, sku)].pop(session_id, None)

    def release_all(self, session_id: str) -> None:
        with self._lock:
            for holds in self._holds.values():
                holds.pop(session_id, None)

    def refresh(self, session_id: str) -> None:
        with self._lock:
            deadline = self.clock() + self.hold_seconds
            for holds in self._holds.values():
                if session_id in holds:
                    holds[session_id].expires_at = deadline

    def commit(self, distributor_id: str, session_id: str, lines: dict[str, int]) -> tuple[bool, dict[str, int]]:
        with self._lock:
            short = {}
            for sku, quantity in lines.items():
                free = self.available(distributor_id, sku, session_id)
                if quantity > free:
                    short[sku] = free
            if short:
                return False, short
            for sku, quantity in lines.items():
                self._committed[(distributor_id, sku)] += quantity
                self._holds[(distributor_id, sku)].pop(session_id, None)
            return True, {}

    def snapshot(self, distributor_id: str) -> list[dict]:
        rows = []
        for sku in sorted(self.dataset.products):
            with self._lock:
                key = (distributor_id, sku)
                self._expire(key)
                held = sum(h.quantity for h in self._holds[key].values())
                committed = self._committed[key]
            rows.append({
                "sku": sku,
                "base": self.base(distributor_id, sku),
                "held": held,
                "committed": committed,
                "available": self.available(distributor_id, sku),
                "status": self.status(distributor_id, sku),
            })
        return rows

    def _expire(self, key: tuple[str, str]) -> None:
        now = self.clock()
        holds = self._holds[key]
        for sid in [sid for sid, h in holds.items() if h.expires_at <= now]:
            del holds[sid]
