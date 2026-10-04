"""Agent B: Jev makes the decisions, Claude only writes (test cases, script, and healing when Jev is unsure)."""
from __future__ import annotations

import json

from typesafe_sdk import Choice, Noul, Score

from .base_agent import BaseAgent
from .config import SETTINGS
from .jev import Jev
from .playwright_runner import replace_locator
from .prompts import SYSTEM_QA, heal_prompt
from .schemas import HealedScript


class HybridAgent(BaseAgent):
    name = "hybrid_jev"

    def __init__(self, llm, jev: Jev, metrics, out_dir, inject_fault=False):
        super().__init__(llm, metrics, out_dir, inject_fault)
        self.jev = jev

    # ---------- readiness: 4 questions, one Jev call ----------
    def decide_readiness(self, ticket):
        r = self.jev.decide("readiness", {"ticket": ticket}, {
            "ready": Noul(instructions="The story describes concrete user actions and expected outcomes clearly enough to write UI test cases without asking questions"),
            "has_ac": Noul(instructions="The story contains explicit acceptance criteria (given/when/then or a checklist of expected behaviours)"),
            "test_type": Choice(instructions="What kind of automated test does this story need", criteria={
                "ui": "Behaviour is visible in a web page: forms, navigation, messages",
                "api": "Behaviour is only observable through HTTP endpoints or data",
                "both": "Needs UI checks and separate API checks",
            }),
            "risk": Score(instructions="How risky is a defect in this feature", criteria=[
                "Cosmetic, no user impact", "Minor inconvenience", "Feature partly broken for some users",
                "Core flow broken for many users", "Blocks every user or loses money/data",
            ]),
        })
        a = r.answers
        return {"ready": a["ready"].noul >= 0.5, "ready_p": round(a["ready"].noul, 3),
                "has_acceptance_criteria": a["has_ac"].noul >= 0.5, "test_type": a["test_type"].choice,
                "risk_1_to_5": round(a["risk"].score, 2), "reason": "jev"}

    # ---------- review: 4 questions per test case, fanned out in ONE call ----------
    def decide_review(self, plan):
        questions = {}
        for tc in plan["test_cases"]:
            i = tc["id"]
            questions[f"{i}__automatable"] = Noul(instructions=f"Test case {i} can be automated with Playwright against a web page using only its listed steps")
            questions[f"{i}__covers"] = Noul(instructions=f"Test case {i} genuinely verifies the acceptance criterion it references")
            questions[f"{i}__duplicate"] = Noul(instructions=f"Test case {i} checks the same behaviour as another test case in the plan")
            questions[f"{i}__priority"] = Choice(instructions=f"Priority of test case {i}", criteria={
                "P1": "Blocks release if it fails: core happy path or security",
                "P2": "Important negative or error-handling path",
                "P3": "Nice to have, edge or cosmetic",
            })
        r = self.jev.decide("review", {"plan": plan}, questions)
        a = r.answers
        return [{
            "id": tc["id"],
            "automatable": a[f"{tc['id']}__automatable"].noul >= 0.5,
            "covers_acceptance_criterion": a[f"{tc['id']}__covers"].noul >= 0.5,
            "priority": a[f"{tc['id']}__priority"].choice,
            "duplicate": a[f"{tc['id']}__duplicate"].noul >= 0.7,
        } for tc in plan["test_cases"]]

    # ---------- triage: 2 questions, one Jev call ----------
    def decide_triage(self, failure):
        r = self.jev.decide("triage", {"failure": failure}, {
            "category": Choice(instructions="Why did this Playwright test fail", criteria={
                "product_bug": "The page loaded and the element was found, but the application behaved differently from the expected result",
                "flaky": "Timing, network or load problem; the same test would likely pass on a retry with no change",
                "locator_changed": "A locator timed out or matched nothing, yet an equivalent element is present in the candidate list",
                "environment": "Site unreachable, browser crash, authentication to the environment failed",
                "test_data": "Credentials, fixtures or input data in the test are wrong",
            }),
            "should_heal": Noul(instructions="Automatically editing the test script (not the product) is the right fix for this failure"),
        })
        a = r.answers
        return {"category": a["category"].choice, "confidence": round(a["category"].confidence, 3),
                "should_heal": a["should_heal"].noul >= 0.5, "probabilities": {k: round(v, 3) for k, v in a["category"].probabilities.items()}}

    # ---------- heal: Jev picks the replacement element; LLM only when Jev is not confident ----------
    def heal(self, script, failure, triage):
        old = failure.get("failed_locator")
        cands = failure.get("candidates", [])
        if triage["category"] == "locator_changed" and old and cands:
            criteria = {f"c{i}": f"{c['selector']}  {c['description']}" for i, c in enumerate(cands)}
            criteria["none"] = "No candidate is the element the old locator pointed to"
            r = self.jev.decide("locator_match", {"old_locator": old, "test": failure["test"], "error": failure["error"][:600]}, {
                "replacement": Choice(instructions="Which candidate element is the same element the old locator used to point to", criteria=criteria),
            })
            ans = r.answers["replacement"]
            if ans.choice != "none" and ans.confidence >= SETTINGS.jev_confidence:
                new = cands[int(ans.choice[1:])]["selector"]
                patched = replace_locator(script, old, new)
                if patched is not None:
                    return patched, f"Jev template heal: {old!r} -> {new!r} (confidence {ans.confidence:.2f}, no LLM call)"
            note = f"Jev not confident ({ans.choice}, {ans.confidence:.2f}); escalating to LLM"
        else:
            note = "no locator/candidates; escalating to LLM"
        healed = self.llm.ask("heal", SYSTEM_QA, heal_prompt(script, failure), HealedScript)
        return healed.code, f"{note}. LLM rewrite: {healed.change_summary}"

    # ---------- report: Jev scores severity, a template writes the prose ----------
    def write_report(self, ticket, plan, runs):
        final = runs[-1]
        failed = [r for r in final["results"] if r["status"] != "passed"]
        severities = {}
        if failed:
            qs = {f"sev{i}": Choice(instructions=f"Severity of the failure of test '{r['name']}'", criteria={
                "critical": "Core flow unusable or data loss", "high": "Major function broken, no workaround",
                "medium": "Function broken with a workaround", "low": "Cosmetic or rare edge case"}) for i, r in enumerate(failed)}
            r = self.jev.decide("report_severity", {"ticket": ticket, "failures": [{"test": f["name"], "error": f.get("error", "")[:500]} for f in failed]}, qs)
            severities = {failed[i]["name"]: r.answers[f"sev{i}"].choice for i in range(len(failed))}
        lines = [f"# QA report for {ticket.get('key')}: {ticket.get('summary')}", "",
                 f"Target: {SETTINGS.base_url}", f"Runs: {len(runs)}", "", "## Final run", ""]
        for r in final["results"]:
            mark = "PASS" if r["status"] == "passed" else "FAIL"
            lines.append(f"- {mark} {r['name']}" + (f" (severity: {severities.get(r['name'])})" if mark == "FAIL" else ""))
        heals = [(r["attempt"], r.get("triage"), r.get("heal")) for r in runs if r.get("heal")]
        if heals:
            lines += ["", "## Healing history", ""]
            for att, tri, how in heals:
                lines.append(f"- run {att}: triage={tri['category']} (p={tri.get('confidence')}); {how}")
        if failed:
            lines += ["", "## Open defects", ""]
            for f in failed:
                lines.append(f"- {f['name']} [{severities.get(f['name'])}]: {f.get('error', '')[:300]}")
        verdict = "PASS: all selected cases pass" if not failed else f"FAIL: {len(failed)} case(s) still failing"
        lines += ["", f"**Verdict:** {verdict}"]
        return "\n".join(lines)
