"""Prompts shared by both agents. The LLM-only agent uses all of them; the hybrid agent uses only the
generation ones (test cases, script, heal) and replaces the decision prompts with Jev questions."""
from __future__ import annotations

import json

SYSTEM_QA = (
    "You are a senior QA automation engineer. You write precise, minimal, deterministic test assets. "
    "You never invent product behaviour that the ticket does not describe."
)

RUNNER_CONTRACT = '''
Script contract (must be followed exactly):
- Plain Python 3 file. Only imports allowed: `import re` and `from playwright.sync_api import Page, expect`. For URL checks use `expect(page).to_have_url(re.compile(...))`.
- Define BASE_URL = "{base_url}" at module level.
- One function per test case: `def test_tc_<n>(page: Page) -> None:` It receives a fresh page, must call
  page.goto(BASE_URL) itself, perform the steps and assert the expected result with `expect(...)` or `assert`.
- Locators: use `page.locator("<css>")` with double-quoted CSS strings, preferring `[data-test="..."]`, then `#id`,
  then `tag[name="..."]`. Do not use getByRole/getByText and do not use XPath. Do not call page.wait_for_timeout.
- At the end define: TESTS = [("TC-1: <title>", test_tc_1), ("TC-2: <title>", test_tc_2), ...] in the same order as the
  test cases. Names must start with the test case id followed by a colon.
- No main block, no playwright launch code, no prints.
'''


def ticket_block(ticket: dict) -> str:
    return "JIRA TICKET\n" + json.dumps(ticket, indent=2, ensure_ascii=False)


def readiness_prompt(ticket: dict) -> str:
    return (
        f"{ticket_block(ticket)}\n\n"
        "Decide whether this story is ready for QA automation. Report whether it has acceptance criteria, "
        "whether it needs UI tests, API tests or both, and a risk score from 1 (trivial) to 5 (critical path)."
    )


def test_plan_prompt(ticket: dict) -> str:
    return (
        f"{ticket_block(ticket)}\n\n"
        "Analyse the requirement. Extract the acceptance criteria as a list. Then write 3 to 5 UI test cases that "
        "together cover every acceptance criterion: at least one positive, at least one negative. Each case must "
        "have concrete steps and one observable expected result. Use ids TC-1, TC-2, ..."
    )


def review_prompt(plan: dict) -> str:
    return (
        "TEST PLAN\n" + json.dumps(plan, indent=2, ensure_ascii=False) + "\n\n"
        "Review every test case: is it automatable with Playwright, does it actually verify the acceptance criterion "
        "it claims, what priority (P1 blocks release, P2 important, P3 nice to have), and is it a duplicate of "
        "another case in this plan. Return one item per test case id."
    )


def script_prompt(ticket: dict, plan: dict, selected_ids: list[str], base_url: str, page_hints: str) -> str:
    cases = [tc for tc in plan["test_cases"] if tc["id"] in selected_ids]
    return (
        f"{ticket_block(ticket)}\n\nTEST CASES TO AUTOMATE\n{json.dumps(cases, indent=2, ensure_ascii=False)}\n\n"
        f"PAGE HINTS (real elements observed on the target page)\n{page_hints}\n\n"
        f"Write the Playwright script.\n{RUNNER_CONTRACT.format(base_url=base_url)}"
    )


def triage_prompt(failure: dict) -> str:
    return (
        "TEST FAILURE\n" + json.dumps(failure, indent=2, ensure_ascii=False) + "\n\n"
        "Classify the failure: product_bug (the app behaves wrongly), flaky (timing or network, a retry would likely pass), "
        "locator_changed (the element exists but the selector no longer matches), environment (site down, auth, browser), "
        "test_data (credentials or fixtures wrong). Say whether the test script should be healed automatically."
    )


def heal_prompt(script: str, failure: dict) -> str:
    return (
        "CURRENT SCRIPT\n```python\n" + script + "\n```\n\n"
        "FAILURE\n" + json.dumps(failure, indent=2, ensure_ascii=False) + "\n\n"
        "Fix the script so the failing test passes against the real page, using the candidate elements listed in the "
        "failure. Change as little as possible. Keep the runner contract and all other tests unchanged. "
        "Return the complete file."
    )


def report_prompt(ticket: dict, plan: dict, runs: list[dict]) -> str:
    return (
        f"{ticket_block(ticket)}\n\nTEST PLAN\n{json.dumps(plan, indent=2, ensure_ascii=False)}\n\n"
        f"RUN HISTORY\n{json.dumps(runs, indent=2, ensure_ascii=False)}\n\n"
        "Write a concise QA report in Markdown: ticket, what was tested, pass/fail per case for the final run, "
        "what was healed and why, open defects with severity, and a one-line verdict."
    )
