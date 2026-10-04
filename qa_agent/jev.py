"""Thin TypeSafe (Jev) wrapper: one `decide` call that records usage, latency and cost."""
from __future__ import annotations

import json
import time
from collections.abc import Mapping

from typesafe_sdk import Choice, Noul, Score, SystemOneResponse, TypeSafeClient

from .metrics import Call, RunMetrics

JEV_PRICE_KEY = "jev"


class Jev:
    backend = "jev"

    def __init__(self, metrics: RunMetrics):
        self.client = TypeSafeClient()
        self.metrics = metrics

    def decide(self, step: str, state, questions: Mapping[str, Choice | Noul | Score]) -> SystemOneResponse:
        t0 = time.perf_counter()
        response = self.client.system_one(state=state, questions=questions)
        seconds = time.perf_counter() - t0
        in_tok = response.usage.input_tokens
        if in_tok is None:  # API did not report usage: estimate from payload size (4 chars/token)
            in_tok = len(json.dumps({"state": state, "questions": {k: q.model_dump() for k, q in questions.items()}})) // 4
        self.metrics.record(Call(step=step, backend=self.backend, model=JEV_PRICE_KEY, seconds=seconds,
                                 input_tokens=in_tok, output_tokens=response.usage.output_tokens or 0,
                                 note=f"model={response.model}"))
        return response


def _words(text: str) -> set[str]:
    import re
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 3}


class _Ans:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Resp:
    def __init__(self, answers):
        self.answers = answers


class MockJev(Jev):
    """Deterministic stand-in for `--mock` runs. Returns high confidence for the first criterion of each
    Choice unless the criterion text contains a hint, and 0.9 for every Noul."""

    def __init__(self, metrics: RunMetrics):
        self.metrics = metrics

    def decide(self, step: str, state, questions):
        time.sleep(0.02)
        answers = {}
        for name, q in questions.items():
            if isinstance(q, Noul):
                answers[name] = _Ans(type="noul", noul=0.9)
            elif isinstance(q, Choice):
                keys = list(q.criteria.keys())
                blob = _words(json.dumps(state))
                focus = _words(str(state.get("old_locator", ""))) if isinstance(state, dict) else set()
                scored = []
                for k in keys:
                    if k == "none":
                        continue
                    toks = _words(str(q.criteria[k]))
                    scored.append((3 * len(toks & focus) + len(toks & blob), k))
                scored.sort(reverse=True)
                pick = scored[0][1] if scored else keys[0]
                if name == "category":  # rule: a locator timeout with candidates present is a locator change
                    pick = "locator_changed" if "waiting for locator" in json.dumps(state) else "product_bug"
                if name == "replacement" and isinstance(state, dict):  # rule: strip a version suffix and look it up
                    import re
                    base = re.sub(r"-v\d+", "", str(state.get("old_locator", "")))
                    hit = [k for k in keys if k != "none" and base and base in str(q.criteria[k])]
                    pick = hit[0] if hit else "none"
                probs = {k: 0.03 for k in keys}
                probs[pick] = 1 - 0.03 * (len(keys) - 1)
                answers[name] = _Ans(type="choice", choice=pick, confidence=probs[pick], probabilities=probs)
            elif isinstance(q, Score):
                n = len(q.criteria)
                answers[name] = _Ans(type="score", score=float(min(3, n)), confidence=0.8,
                                     probabilities={i + 1: 1 / n for i in range(n)})
        approx = len(json.dumps(state)) // 4
        self.metrics.record(Call(step=step, backend=self.backend, model=JEV_PRICE_KEY, seconds=0.25,
                                 input_tokens=approx, note="mock"))
        return _Resp(answers)
