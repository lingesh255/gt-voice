from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import openpyxl

LANGUAGE_CODES = {
    "Hindi": "hi-IN",
    "English": "en-IN",
    "Tamil": "ta-IN",
    "Gujarati": "gu-IN",
    "Marathi": "mr-IN",
    "Telugu": "te-IN",
    "Kannada": "kn-IN",
    "Malayalam": "ml-IN",
    "Bengali": "bn-IN",
    "Odia": "od-IN",
    "Assamese": "as-IN",
    "Punjabi": "pa-IN",
}

LANGUAGE_NAMES = {code: name for name, code in LANGUAGE_CODES.items()}

SHEETS = {
    "Retailer_Master": [
        "Retailer_ID", "Retailer_Name", "City", "State", "Preferred_Language", "Outlet_Type",
        "Retailer_Segment", "Distributor_ID", "Synthetic_Contact_ID", "Estimated_Monthly_Sales_INR",
    ],
    "Product_Master": [
        "SKU_ID", "Brand", "Product_Name", "Pack_Size", "Category", "MRP_INR",
        "Distributor_Billing_Price_INR", "Selling_Unit", "MOQ_Units",
    ],
    "Purchase_History": [
        "Transaction_ID", "Retailer_ID", "SKU_ID", "Order_Date", "Last_Order_Quantity_Units",
        "Previous_Order_Quantity_Units", "Average_Monthly_Quantity_Units", "Days_Since_Last_Order",
    ],
    "Distributor_Inventory": [
        "Distributor_ID", "SKU_ID", "Available_Stock_Units", "Stock_Status", "Last_Updated",
    ],
    "Retailer_Preferences": [
        "Retailer_ID", "Preferred_Calling_Window", "Preferred_Language", "Preferred_Brands",
        "Purchase_Frequency_Days", "Typical_Order_Value_INR", "Cross_Sell_Opportunity", "Do_Not_Call_Flag",
    ],
    "Call_Scenarios": [
        "Scenario_ID", "Scenario_Name", "Retailer_Statement_or_Setup", "Expected_Agent_Behaviour", "Pass_Criteria",
    ],
}

PACK_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|litre|liter|ltr|l|kg|gm|g)\b", re.IGNORECASE)
PACK_FACTORS = {"ml": (1, "ml"), "l": (1000, "ml"), "litre": (1000, "ml"), "liter": (1000, "ml"),
                "ltr": (1000, "ml"), "g": (1, "g"), "gm": (1, "g"), "kg": (1000, "g")}


class DataError(Exception):
    pass


def parse_pack(text: str) -> tuple[int, str]:
    match = PACK_PATTERN.search(text or "")
    if not match:
        raise DataError(f"Cannot read pack size {text!r}")
    factor, measure = PACK_FACTORS[match.group(2).lower()]
    return round(float(match.group(1)) * factor), measure


def to_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()


def to_window(value: str) -> tuple[time, time]:
    start, end = str(value).split("-")
    return datetime.strptime(start.strip(), "%H:%M").time(), datetime.strptime(end.strip(), "%H:%M").time()


@dataclass(frozen=True)
class Retailer:
    id: str
    name: str
    city: str
    state: str
    language: str
    outlet_type: str
    segment: str
    distributor_id: str
    contact_id: str
    monthly_sales: int

    @property
    def language_code(self) -> str:
        return LANGUAGE_CODES.get(self.language, "en-IN")


@dataclass(frozen=True)
class Preferences:
    retailer_id: str
    window_start: time
    window_end: time
    language: str
    brands: tuple[str, ...]
    frequency_days: int
    typical_order_value: int
    cross_sell: str
    do_not_call: bool

    @property
    def window(self) -> str:
        return f"{self.window_start:%H:%M}-{self.window_end:%H:%M}"


@dataclass(frozen=True)
class Product:
    sku: str
    brand: str
    name: str
    pack_size: str
    category: str
    mrp: int
    price: int
    selling_unit: str
    moq: int
    pack_amount: int
    pack_measure: str

    @property
    def label(self) -> str:
        return f"{self.name} {self.pack_size}"


@dataclass(frozen=True)
class PurchaseLine:
    transaction_id: str
    retailer_id: str
    sku: str
    order_date: date
    last_qty: int
    previous_qty: int
    avg_monthly_qty: int
    days_since: int


@dataclass(frozen=True)
class StockRow:
    distributor_id: str
    sku: str
    available: int
    status: str
    updated: date


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    setup: str
    expected: str
    pass_criteria: str


class Dataset:
    def __init__(self, retailers, preferences, products, purchases, stock, scenarios, readme):
        self.retailers: dict[str, Retailer] = retailers
        self.preferences: dict[str, Preferences] = preferences
        self.products: dict[str, Product] = products
        self.stock: dict[tuple[str, str], StockRow] = stock
        self.scenarios: list[Scenario] = scenarios
        self.readme: dict[str, str] = readme
        self._purchases: dict[str, list[PurchaseLine]] = defaultdict(list)
        for line in purchases:
            self._purchases[line.retailer_id].append(line)
        self.purchase_rows = len(purchases)
        anchors = Counter(line.order_date + timedelta(days=line.days_since) for line in purchases)
        self.as_of: date = anchors.most_common(1)[0][0] if anchors else date.today()
        low = [row.available for row in stock.values() if row.status.lower().startswith("low")]
        self.low_stock_threshold = max(low) if low else 15

    @classmethod
    def load(cls, path: str | Path) -> Dataset:
        path = Path(path)
        if not path.exists():
            raise DataError(f"Data file not found: {path}")
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            tables = {name: list(_rows(book, name, columns)) for name, columns in SHEETS.items()}
            readme = _readme(book)
        finally:
            book.close()

        retailers = {}
        for row in tables["Retailer_Master"]:
            retailer = Retailer(
                id=_text(row["Retailer_ID"]),
                name=_text(row["Retailer_Name"]),
                city=_text(row["City"]),
                state=_text(row["State"]),
                language=_text(row["Preferred_Language"]),
                outlet_type=_text(row["Outlet_Type"]),
                segment=_text(row["Retailer_Segment"]),
                distributor_id=_text(row["Distributor_ID"]),
                contact_id=_text(row["Synthetic_Contact_ID"]),
                monthly_sales=int(row["Estimated_Monthly_Sales_INR"] or 0),
            )
            retailers[retailer.id] = retailer

        preferences = {}
        for row in tables["Retailer_Preferences"]:
            start, end = to_window(row["Preferred_Calling_Window"])
            brands = tuple(b.strip() for b in _text(row["Preferred_Brands"]).split(",") if b.strip())
            pref = Preferences(
                retailer_id=_text(row["Retailer_ID"]),
                window_start=start,
                window_end=end,
                language=_text(row["Preferred_Language"]),
                brands=brands,
                frequency_days=int(row["Purchase_Frequency_Days"]),
                typical_order_value=int(row["Typical_Order_Value_INR"]),
                cross_sell=_text(row["Cross_Sell_Opportunity"]),
                do_not_call=_text(row["Do_Not_Call_Flag"]).lower() in {"yes", "y", "true", "1"},
            )
            preferences[pref.retailer_id] = pref

        products = {}
        for row in tables["Product_Master"]:
            amount, measure = parse_pack(_text(row["Pack_Size"]))
            product = Product(
                sku=_text(row["SKU_ID"]),
                brand=_text(row["Brand"]),
                name=_text(row["Product_Name"]),
                pack_size=_text(row["Pack_Size"]),
                category=_text(row["Category"]),
                mrp=int(row["MRP_INR"]),
                price=int(row["Distributor_Billing_Price_INR"]),
                selling_unit=_text(row["Selling_Unit"]),
                moq=int(row["MOQ_Units"] or 1),
                pack_amount=amount,
                pack_measure=measure,
            )
            products[product.sku] = product

        purchases = [
            PurchaseLine(
                transaction_id=_text(row["Transaction_ID"]),
                retailer_id=_text(row["Retailer_ID"]),
                sku=_text(row["SKU_ID"]),
                order_date=to_date(row["Order_Date"]),
                last_qty=int(row["Last_Order_Quantity_Units"]),
                previous_qty=int(row["Previous_Order_Quantity_Units"]),
                avg_monthly_qty=int(row["Average_Monthly_Quantity_Units"]),
                days_since=int(row["Days_Since_Last_Order"]),
            )
            for row in tables["Purchase_History"]
        ]

        stock = {}
        for row in tables["Distributor_Inventory"]:
            item = StockRow(
                distributor_id=_text(row["Distributor_ID"]),
                sku=_text(row["SKU_ID"]),
                available=int(row["Available_Stock_Units"]),
                status=_text(row["Stock_Status"]),
                updated=to_date(row["Last_Updated"]),
            )
            stock[(item.distributor_id, item.sku)] = item

        scenarios = [
            Scenario(
                id=_text(row["Scenario_ID"]),
                name=_text(row["Scenario_Name"]),
                setup=_text(row["Retailer_Statement_or_Setup"]),
                expected=_text(row["Expected_Agent_Behaviour"]),
                pass_criteria=_text(row["Pass_Criteria"]),
            )
            for row in tables["Call_Scenarios"]
        ]

        return cls(retailers, preferences, products, purchases, stock, scenarios, readme)

    def retailer(self, retailer_id: str) -> Retailer:
        try:
            return self.retailers[retailer_id.strip().upper()]
        except KeyError:
            raise DataError(f"Unknown retailer {retailer_id!r}") from None

    def prefs(self, retailer_id: str) -> Preferences:
        return self.preferences[retailer_id]

    def product(self, sku: str) -> Product:
        try:
            return self.products[sku.strip().upper()]
        except KeyError:
            raise DataError(f"Unknown SKU {sku!r}") from None

    def purchases(self, retailer_id: str) -> list[PurchaseLine]:
        return list(self._purchases.get(retailer_id, []))

    def purchase(self, retailer_id: str, sku: str) -> PurchaseLine | None:
        for line in self._purchases.get(retailer_id, []):
            if line.sku == sku:
                return line
        return None

    def stock_row(self, distributor_id: str, sku: str) -> StockRow | None:
        return self.stock.get((distributor_id, sku))

    def by_contact(self, contact_id: str) -> Retailer | None:
        for retailer in self.retailers.values():
            if retailer.contact_id == contact_id:
                return retailer
        return None

    def pack_sizes(self) -> set[tuple[int, str]]:
        return {(p.pack_amount, p.pack_measure) for p in self.products.values()}

    def family(self, product: Product) -> list[Product]:
        return sorted(
            (p for p in self.products.values() if p.name == product.name),
            key=lambda p: p.pack_amount,
        )


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _rows(book, sheet: str, columns: list[str]):
    if sheet not in book.sheetnames:
        raise DataError(f"Sheet {sheet!r} is missing from the workbook")
    rows = book[sheet].iter_rows(values_only=True)
    header = [_text(cell) for cell in next(rows)]
    missing = [c for c in columns if c not in header]
    if missing:
        raise DataError(f"Sheet {sheet!r} is missing columns: {', '.join(missing)}")
    index = {name: header.index(name) for name in columns}
    for row in rows:
        if row is None or all(cell is None for cell in row):
            continue
        yield {name: row[i] if i < len(row) else None for name, i in index.items()}


def _readme(book) -> dict[str, str]:
    if "README" not in book.sheetnames:
        return {}
    notes = {}
    for row in book["README"].iter_rows(values_only=True):
        if row and row[0] and len(row) > 1 and row[1]:
            notes[_text(row[0])] = _text(row[1])
    return notes
