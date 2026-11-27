from __future__ import annotations

from dataclasses import dataclass, field

from .data import Product

DRAFT = "draft"
READ_BACK = "read_back"
CONFIRMED = "confirmed"
CANCELLED = "cancelled"


class OrderError(Exception):
    pass


@dataclass
class OrderLine:
    product: Product
    quantity: int
    source: str
    said: str = ""

    @property
    def value(self) -> int:
        return self.quantity * self.product.price

    def as_dict(self) -> dict:
        return {
            "sku": self.product.sku,
            "product": self.product.label,
            "quantity": self.quantity,
            "unit": self.product.selling_unit,
            "unit_price_inr": self.product.price,
            "line_value_inr": self.value,
            "source": self.source,
        }


@dataclass
class Order:
    lines: dict[str, OrderLine] = field(default_factory=dict)
    state: str = DRAFT
    revision: int = 0
    read_back_revision: int | None = None

    @property
    def total(self) -> int:
        return sum(line.value for line in self.lines.values())

    @property
    def is_open(self) -> bool:
        return self.state in (DRAFT, READ_BACK)

    def _touch(self) -> None:
        self.revision += 1
        if self.state == READ_BACK:
            self.state = DRAFT
        self.read_back_revision = None

    def _require_open(self) -> None:
        if not self.is_open:
            raise OrderError(f"The order is already {self.state}.")

    def add(self, product: Product, quantity: int, source: str, said: str = "") -> OrderLine:
        self._require_open()
        if product.sku in self.lines:
            raise OrderError(f"{product.label} is already in the order with {self.lines[product.sku].quantity}. "
                             f"Use change_quantity with the new total.")
        line = OrderLine(product, quantity, source, said)
        self.lines[product.sku] = line
        self._touch()
        return line

    def change(self, sku: str, quantity: int, said: str = "") -> OrderLine:
        self._require_open()
        if sku not in self.lines:
            raise OrderError(f"{sku} is not in the order yet. Use add_to_order.")
        line = self.lines[sku]
        line.quantity = quantity
        line.said = said or line.said
        self._touch()
        return line

    def remove(self, sku: str) -> OrderLine:
        self._require_open()
        if sku not in self.lines:
            raise OrderError(f"{sku} is not in the order.")
        line = self.lines.pop(sku)
        self._touch()
        return line

    def read_back(self) -> None:
        self._require_open()
        if not self.lines:
            raise OrderError("The order is empty, there is nothing to read back.")
        self.state = READ_BACK
        self.read_back_revision = self.revision

    def confirm(self) -> None:
        if self.state != READ_BACK or self.read_back_revision != self.revision:
            raise OrderError("The order changed after the last read-back (or was never read back). "
                             "Call read_back_order and read the full order again first.")
        self.state = CONFIRMED

    def cancel(self) -> None:
        if self.state == CONFIRMED:
            raise OrderError("A confirmed order cannot be cancelled on the call.")
        self.state = CANCELLED

    def summary(self) -> dict:
        return {
            "state": self.state,
            "revision": self.revision,
            "lines": [line.as_dict() for line in self.lines.values()],
            "total_inr": self.total,
        }
