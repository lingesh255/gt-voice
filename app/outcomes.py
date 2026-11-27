from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

FILES = {
    "orders": "orders.jsonl",
    "tasks": "tasks.jsonl",
    "insights": "insights.jsonl",
    "opt_outs": "do_not_call.jsonl",
    "calls": "calls.jsonl",
}

PREFIXES = {"callback": "CB", "escalation": "ES", "complaint": "CP", "insight": "MI"}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class OutcomeStore:
    def __init__(self, folder: str | Path | None = None):
        self.folder = Path(folder) if folder else None
        self._lock = threading.Lock()
        self.records: dict[str, list[dict]] = {kind: [] for kind in FILES}
        if self.folder:
            self.folder.mkdir(parents=True, exist_ok=True)
            for kind, name in FILES.items():
                path = self.folder / name
                if path.exists():
                    for line in path.read_text(encoding="utf-8").splitlines():
                        if line.strip():
                            try:
                                self.records[kind].append(json.loads(line))
                            except json.JSONDecodeError:
                                continue

    def add(self, kind: str, record: dict) -> dict:
        with self._lock:
            record = dict(record)
            record.setdefault("created_at", now_iso())
            self.records[kind].append(record)
            if self.folder:
                with open(self.folder / FILES[kind], "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            return record

    def list(self, kind: str) -> list[dict]:
        with self._lock:
            return list(self.records[kind])

    def opted_out(self) -> set[str]:
        with self._lock:
            return {r["retailer_id"] for r in self.records["opt_outs"]}

    def next_task_id(self, kind: str) -> str:
        prefix = PREFIXES.get(kind, "TK")
        with self._lock:
            pool = self.records["insights"] if kind == "insight" else self.records["tasks"]
            used = sum(1 for r in pool if str(r.get("id", "")).startswith(prefix + "-"))
        return f"{prefix}-{used + 1:04d}"

    def next_order_id(self, day: datetime | None = None) -> str:
        day = day or datetime.now()
        stamp = day.strftime("%y%m%d")
        with self._lock:
            used = sum(1 for r in self.records["orders"] if str(r.get("order_id", "")).startswith(f"SO-{stamp}-"))
        return f"SO-{stamp}-{used + 1:04d}"
