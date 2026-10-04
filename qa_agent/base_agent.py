"""The pipeline both agents share. Subclasses only override the *decision* steps.

Steps: fetch ticket -> readiness -> test plan (LLM) -> review -> script (LLM) -> run -> on failure: triage,
heal (template or LLM), rerun -> report.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import SETTINGS
from .llm import LLM
from .metrics import RunMetrics
from .playwright_runner import break_one_selector, page_hints, run_script
from .prompts import SYSTEM_QA, script_prompt, test_plan_prompt
from .schemas import GeneratedScript, TestPlan


class BaseAgent:
    name = "base"

    def __init__(self, llm: LLM, metrics: RunMetrics, out_dir: Path, inject_fault: bool = False):
        self.llm = llm
        self.metrics = metrics
        self.out = out_dir
        self.out.mkdir(parents=True, exist_ok=True)
        self.inject_fault = inject_fault
        self.log: list[str] = []

    # ---------- decision steps (overridden by subclasses) ----------
    def decide_readiness(self, ticket: dict) -> dict: ...
    def decide_review(self, plan: dict) -> list[dict]: ...
    def decide_triage(self, failure: dict) -> dict: ...
    def heal(self, script: str, failure: dict, triage: dict) -> tuple[str, str]: ...
    def write_report(self, ticket: dict, plan: dict, runs: list[dict]) -> str: ...

    # ---------- shared generation steps ----------
    def make_plan(self, ticket: dict) -> dict:
        plan = self.llm.ask("test_plan", SYSTEM_QA, test_plan_prompt(ticket), TestPlan)
        return plan.model_dump()

    def make_script(self, ticket: dict, plan: dict, selected: list[str], hints: str) -> str:
        gen = self.llm.ask("script_generation", SYSTEM_QA,
                           script_prompt(ticket, plan, selected, SETTINGS.base_url, hints), GeneratedScript)
        return gen.code

    # ---------- orchestration ----------
    def say(self, msg: str) -> None:
        line = f"[{self.name}] {datetime.now().strftime('%H:%M:%S')} {msg}"
        self.log.append(line)
        print(line, flush=True)

    def save(self, name: str, data) -> None:
        p = self.out / name
        p.write_text(data if isinstance(data, str) else json.dumps(data, indent=2, ensure_ascii=False))

    def run(self, ticket: dict) -> dict:
        m = self.metrics
        self.save("ticket.json", ticket)

        with m.step("readiness"):
            readiness = self.decide_readiness(ticket)
        self.save("readiness.json", readiness)
        self.say(f"readiness: {readiness}")
        if not readiness.get("ready", True):
            self.say("ticket judged not ready; stopping")
            m.finish()
            return {"stopped": "not_ready", "readiness": readiness}

        with m.step("test_plan"):
            plan = self.make_plan(ticket)
        self.save("test_plan.json", plan)
        self.say(f"test plan: {len(plan['test_cases'])} cases")

        with m.step("review"):
            review = self.decide_review(plan)
        self.save("review.json", review)
        selected = [r["id"] for r in review if r["automatable"] and not r["duplicate"]]
        if not selected:
            selected = [tc["id"] for tc in plan["test_cases"]]
        self.say(f"review kept {len(selected)}/{len(plan['test_cases'])} cases: {selected}")

        with m.step("page_hints"):
            hints = page_hints(SETTINGS.base_url)
        with m.step("script_generation"):
            script = self.make_script(ticket, plan, selected, hints)
        fault_note = ""
        if self.inject_fault:
            script, fault_note = break_one_selector(script)
            self.say(f"fault injected: {fault_note}")
        self.save("script_v1.py", script)

        runs: list[dict] = []
        attempt = 1
        while True:
            with m.step("execution"):
                result = run_script(self.out / f"script_v{attempt}.py", self.out / f"run_{attempt}.json", SETTINGS.test_timeout_ms)
            results = result.get("results", [])
            failed = [r for r in results if r["status"] != "passed"]
            runs.append({"attempt": attempt, "passed": len(results) - len(failed), "failed": len(failed),
                         "import_error": result.get("import_error"), "results": results})
            self.say(f"run {attempt}: {len(results) - len(failed)} passed, {len(failed)} failed")
            if not failed and not result.get("import_error"):
                break
            if attempt > SETTINGS.max_heal_attempts:
                self.say("heal budget exhausted")
                break

            failure = self._failure_payload(failed[0] if failed else {"name": "import", "error": result.get("import_error", "")})
            with m.step("triage"):
                triage = self.decide_triage(failure)
            self.say(f"triage: {triage}")
            runs[-1]["triage"] = triage
            if triage["category"] == "product_bug" or not triage.get("should_heal", False):
                self.say("not healing (product bug or heal not advised)")
                break
            if triage["category"] == "flaky":
                script_next, how = script, "rerun without changes (flaky)"
            else:
                with m.step("heal"):
                    script_next, how = self.heal(script, failure, triage)
            runs[-1]["heal"] = how
            self.say(f"heal: {how}")
            attempt += 1
            script = script_next
            self.save(f"script_v{attempt}.py", script)

        with m.step("report"):
            report = self.write_report(ticket, plan, runs)
        self.save("report.md", report)
        m.finish()
        m.save(self.out / "metrics.json")
        self.save("log.txt", "\n".join(self.log))
        summary = {"agent": self.name, "fault": fault_note, "runs": [{k: v for k, v in r.items() if k != "results"} for r in runs],
                   "final_passed": runs[-1]["passed"], "final_failed": runs[-1]["failed"], "metrics": m.to_dict()}
        self.save("summary.json", summary)
        return summary

    @staticmethod
    def _failure_payload(item: dict) -> dict:
        return {
            "test": item.get("name"),
            "error": item.get("error", "")[:1500],
            "failed_locator": item.get("failed_locator"),
            "url": item.get("url"),
            "candidates": [{"selector": c["selector"], "description": c["description"]} for c in item.get("candidates", [])[:40]],
        }
