"""Per-step timing and cost accounting. Both agents record into the same shape so runs are comparable."""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import price_for


@dataclass
class Call:
    step: str
    backend: str          # "llm" | "jev" | "none"
    model: str
    seconds: float
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    note: str = ""


@dataclass
class RunMetrics:
    agent: str
    calls: list[Call] = field(default_factory=list)
    step_seconds: dict[str, float] = field(default_factory=dict)
    started: float = field(default_factory=time.perf_counter)
    total_seconds: float = 0.0

    def record(self, call: Call) -> None:
        p = price_for(call.model) if call.backend != "none" else {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        call.cost_usd = (
            call.input_tokens * p["input"]
            + call.output_tokens * p["output"]
            + call.cache_read_tokens * p["cache_read"]
            + call.cache_write_tokens * p["cache_write"]
        ) / 1_000_000
        self.calls.append(call)

    @contextmanager
    def step(self, name: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.step_seconds[name] = self.step_seconds.get(name, 0.0) + (time.perf_counter() - t0)

    def finish(self) -> None:
        self.total_seconds = time.perf_counter() - self.started

    # ---- summaries ----
    def by_backend(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for c in self.calls:
            b = out.setdefault(c.backend, {"calls": 0, "seconds": 0.0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
            b["calls"] += 1
            b["seconds"] += c.seconds
            b["input_tokens"] += c.input_tokens + c.cache_read_tokens + c.cache_write_tokens
            b["output_tokens"] += c.output_tokens
            b["cost_usd"] += c.cost_usd
        return out

    def total_cost(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "total_seconds": round(self.total_seconds, 3),
            "total_cost_usd": round(self.total_cost(), 6),
            "by_backend": {k: {kk: (round(vv, 6) if isinstance(vv, float) else vv) for kk, vv in v.items()} for k, v in self.by_backend().items()},
            "step_seconds": {k: round(v, 3) for k, v in self.step_seconds.items()},
            "calls": [asdict(c) for c in self.calls],
        }

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2))
