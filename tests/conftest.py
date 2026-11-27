from pathlib import Path

import pytest

from app.data import Dataset
from app.inventory import StockLedger
from app.oms import OrderGateway
from app.outcomes import OutcomeStore
from app.session import CallSession, Context

DATA = Path(__file__).resolve().parent.parent / "data" / "Marico_Synthetic_Data_Final_Excel_Data.xlsx"


@pytest.fixture(scope="session")
def dataset() -> Dataset:
    return Dataset.load(DATA)


@pytest.fixture
def ctx(dataset, tmp_path) -> Context:
    store = OutcomeStore(tmp_path / "runs")
    return Context(dataset, StockLedger(dataset), store, OrderGateway(store))


@pytest.fixture
def call(ctx):
    def start(retailer_id: str, confirmed: bool = True) -> CallSession:
        session = CallSession(ctx, retailer_id)
        if confirmed:
            assert session.call_tool("confirm_identity", {"is_right_shop": True})["ok"]
        return session
    return start


def add(session, sku, quantity, said, unit="piece", **extra):
    args = {"sku": sku, "quantity": quantity, "unit": unit, "retailer_said": said, "source": "retailer_request"}
    args.update(extra)
    return session.call_tool("add_to_order", args)


def change(session, sku, quantity, said, unit="piece", **extra):
    args = {"sku": sku, "quantity": quantity, "unit": unit, "retailer_said": said}
    args.update(extra)
    return session.call_tool("change_quantity", args)


def place(session, said="haan, confirm kar do"):
    assert session.call_tool("read_back_order", {})["ok"]
    return session.call_tool("confirm_order", {"retailer_said": said})
