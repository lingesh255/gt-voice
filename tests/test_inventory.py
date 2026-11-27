from app.inventory import StockLedger


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_holds_block_other_sessions(dataset):
    ledger = StockLedger(dataset)
    base = ledger.available("D001", "P006")
    ok, _ = ledger.hold("D001", "P006", "a", base)
    assert ok
    assert ledger.available("D001", "P006", "b") == 0
    assert ledger.available("D001", "P006", "a") == base
    ok, free = ledger.hold("D001", "P006", "b", 1)
    assert not ok and free == 0


def test_release_and_commit(dataset):
    ledger = StockLedger(dataset)
    base = ledger.available("D001", "P001")
    ledger.hold("D001", "P001", "a", 10)
    ledger.release_all("a")
    assert ledger.available("D001", "P001") == base
    ledger.hold("D001", "P001", "a", 10)
    ok, short = ledger.commit("D001", "a", {"P001": 10})
    assert ok and not short
    assert ledger.available("D001", "P001") == base - 10


def test_holds_expire(dataset):
    clock = Clock()
    ledger = StockLedger(dataset, hold_seconds=60, clock=clock)
    base = ledger.available("D001", "P006")
    ledger.hold("D001", "P006", "a", base)
    assert ledger.available("D001", "P006", "b") == 0
    clock.now = 61
    assert ledger.available("D001", "P006", "b") == base


def test_commit_fails_if_stock_moved(dataset):
    clock = Clock()
    ledger = StockLedger(dataset, hold_seconds=60, clock=clock)
    base = ledger.available("D001", "P006")
    ledger.hold("D001", "P006", "a", base)
    clock.now = 61
    ledger.hold("D001", "P006", "b", base)
    ok, short = ledger.commit("D001", "a", {"P006": base})
    assert not ok and short == {"P006": 0}


def test_status_follows_threshold(dataset):
    ledger = StockLedger(dataset)
    assert ledger.status("D001", "P012") == "Out of stock"
    assert ledger.status("D001", "P006") == "Low stock"
    assert ledger.status("D001", "P001") == "Available"
