"""Agent A: every decision and every generation step is a Claude call."""
from __future__ import annotations

from .base_agent import BaseAgent
from .prompts import SYSTEM_QA, heal_prompt, readiness_prompt, report_prompt, review_prompt, triage_prompt
from .schemas import HealedScript, Readiness, Report, Review, Triage


class LLMOnlyAgent(BaseAgent):
    name = "llm_only"

    def decide_readiness(self, ticket):
        return self.llm.ask("readiness", SYSTEM_QA, readiness_prompt(ticket), Readiness).model_dump()

    def decide_review(self, plan):
        return [i.model_dump() for i in self.llm.ask("review", SYSTEM_QA, review_prompt(plan), Review).items]

    def decide_triage(self, failure):
        return self.llm.ask("triage", SYSTEM_QA, triage_prompt(failure), Triage).model_dump()

    def heal(self, script, failure, triage):
        healed = self.llm.ask("heal", SYSTEM_QA, heal_prompt(script, failure), HealedScript)
        return healed.code, f"LLM rewrite: {healed.change_summary}"

    def write_report(self, ticket, plan, runs):
        return self.llm.ask("report", SYSTEM_QA, report_prompt(ticket, plan, runs), Report).markdown
