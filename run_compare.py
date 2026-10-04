"""Run both agents on the same Jira ticket and write a side-by-side comparison.

Examples
  python run_compare.py --ticket DEMO-1                      # fetch from Jira through MCP
  python run_compare.py --ticket-file samples/sample_ticket.json --fault
  python run_compare.py --ticket-file samples/sample_ticket.json --fault --mock   # no API keys needed
  python run_compare.py --ticket DEMO-1 --agent hybrid       # only one agent
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from qa_agent.agent_hybrid import HybridAgent
from qa_agent.agent_llm_only import LLMOnlyAgent
from qa_agent.config import SETTINGS
from qa_agent.jev import Jev, MockJev
from qa_agent.jira_mcp import fetch_ticket, load_ticket_file
from qa_agent.llm import MockLLM, make_llm
from qa_agent.metrics import RunMetrics


def build(agent: str, out: Path, mock: bool, fault: bool):
    cls = LLMOnlyAgent if agent == "llm_only" else HybridAgent
    m = RunMetrics(agent=cls.name)
    if mock:
        from qa_agent.mock_data import CANNED
        llm = MockLLM(m, CANNED)
    else:
        llm = make_llm(m)
    if cls is LLMOnlyAgent:
        return LLMOnlyAgent(llm, m, out / cls.name, inject_fault=fault)
    jev = MockJev(m) if mock else Jev(m)
    return HybridAgent(llm, jev, m, out / cls.name, inject_fault=fault)


def comparison_md(summaries: dict[str, dict], ticket: dict) -> str:
    rows = []
    for name, s in summaries.items():
        mt = s["metrics"]
        bb = mt["by_backend"]
        llm = bb.get("llm", {"calls": 0, "seconds": 0, "cost_usd": 0})
        jev = bb.get("jev", {"calls": 0, "seconds": 0, "cost_usd": 0})
        rows.append(f"| {name} | {mt['total_seconds']:.1f} | ${mt['total_cost_usd']:.4f} | {llm['calls']} / {llm['seconds']:.1f}s / ${llm['cost_usd']:.4f} "
                    f"| {jev['calls']} / {jev['seconds']:.2f}s / ${jev['cost_usd']:.5f} | {len(s['runs'])} | {s['final_passed']} / {s['final_failed']} |")
    lines = [f"# Comparison for {ticket.get('key')}: {ticket.get('summary')}", "",
             f"Model: {SETTINGS.llm_model} (effort {SETTINGS.llm_effort}). Jev confidence gate: {SETTINGS.jev_confidence}.", "",
             "| Agent | Wall time (s) | Total cost | LLM calls / time / cost | Jev calls / time / cost | Runs | Final pass / fail |",
             "|---|---|---|---|---|---|---|", *rows, "", "## Decision steps: who answered", ""]
    steps = ["readiness", "review", "triage", "heal", "locator_match", "report", "report_severity"]
    lines.append("| Step | " + " | ".join(summaries) + " |")
    lines.append("|---|" + "---|" * len(summaries))
    for st in steps:
        cells = []
        for s in summaries.values():
            calls = [c for c in s["metrics"]["calls"] if c["step"] == st]
            if not calls:
                cells.append("-")
            else:
                cells.append(", ".join(f"{c['backend']} {c['seconds']:.2f}s ${c['cost_usd']:.5f}" for c in calls))
        lines.append(f"| {st} | " + " | ".join(cells) + " |")
    lines += ["", "## Per-step wall time (s)", "", "| Step | " + " | ".join(summaries) + " |", "|---|" + "---|" * len(summaries)]
    all_steps = sorted({k for s in summaries.values() for k in s["metrics"]["step_seconds"]})
    for st in all_steps:
        lines.append(f"| {st} | " + " | ".join(f"{s['metrics']['step_seconds'].get(st, 0):.2f}" for s in summaries.values()) + " |")
    lines += ["", "## Healing", ""]
    for name, s in summaries.items():
        for r in s["runs"]:
            if r.get("heal"):
                lines.append(f"- {name} run {r['attempt']}: {r['heal']}")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticket", help="Jira issue key, fetched through the mcp-atlassian MCP server")
    ap.add_argument("--ticket-file", help="Local JSON ticket instead of Jira (see samples/)")
    ap.add_argument("--agent", choices=["both", "llm_only", "hybrid"], default="both")
    ap.add_argument("--fault", action="store_true", help="Break one selector in the generated script to simulate a UI change")
    ap.add_argument("--mock", action="store_true", help="Use canned LLM/Jev answers (no API keys, no cost)")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()
    if not args.ticket and not args.ticket_file:
        ap.error("give --ticket KEY or --ticket-file path")

    ticket = load_ticket_file(args.ticket_file) if args.ticket_file else fetch_ticket(args.ticket)
    print(f"ticket {ticket['key']}: {ticket['summary']}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) / stamp
    agents = ["llm_only", "hybrid"] if args.agent == "both" else [args.agent]
    summaries = {}
    for a in agents:
        agent = build(a, out, args.mock, args.fault)
        summaries[agent.name] = agent.run(ticket)

    (out / "comparison.json").write_text(json.dumps(summaries, indent=2))
    md = comparison_md(summaries, ticket)
    (out / "comparison.md").write_text(md)
    print("\n" + md)
    print(f"results in {out}")


if __name__ == "__main__":
    main()
