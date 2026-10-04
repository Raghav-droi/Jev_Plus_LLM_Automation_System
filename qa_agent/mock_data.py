"""Canned LLM answers for `--mock` runs (saucedemo login story). Lets you exercise the whole loop,
including fault injection and healing, with no API keys."""
from __future__ import annotations

PLAN = {
    "summary": "Login flow on saucedemo: valid login lands on Products, bad password and empty fields show errors.",
    "acceptance_criteria": [
        "Valid credentials land on /inventory.html with title Products",
        "Wrong password shows 'Username and password do not match'",
        "Empty fields show 'Username is required'",
    ],
    "test_cases": [
        {"id": "TC-1", "title": "Valid login shows Products page", "type": "positive", "preconditions": ["On login page"],
         "steps": ["Enter standard_user", "Enter secret_sauce", "Click Login"],
         "expected_result": "URL contains /inventory.html and title text is Products",
         "acceptance_criterion": "Valid credentials land on /inventory.html with title Products"},
        {"id": "TC-2", "title": "Wrong password shows error", "type": "negative", "preconditions": ["On login page"],
         "steps": ["Enter standard_user", "Enter wrong_pass", "Click Login"],
         "expected_result": "Error contains 'Username and password do not match'",
         "acceptance_criterion": "Wrong password shows 'Username and password do not match'"},
        {"id": "TC-3", "title": "Empty fields show required error", "type": "negative", "preconditions": ["On login page"],
         "steps": ["Click Login with empty fields"], "expected_result": "Error contains 'Username is required'",
         "acceptance_criterion": "Empty fields show 'Username is required'"},
    ],
}

SCRIPT = '''import re

from playwright.sync_api import Page, expect

BASE_URL = "https://www.saucedemo.com"


def test_tc_1(page: Page) -> None:
    page.goto(BASE_URL)
    page.locator("[data-test=\\"username\\"]").fill("standard_user")
    page.locator("[data-test=\\"password\\"]").fill("secret_sauce")
    page.locator("[data-test=\\"login-button\\"]").click()
    expect(page).to_have_url(re.compile(r".*/inventory\\.html"))
    expect(page.locator("[data-test=\\"title\\"]")).to_have_text("Products")


def test_tc_2(page: Page) -> None:
    page.goto(BASE_URL)
    page.locator("[data-test=\\"username\\"]").fill("standard_user")
    page.locator("[data-test=\\"password\\"]").fill("wrong_pass")
    page.locator("[data-test=\\"login-button\\"]").click()
    expect(page.locator("[data-test=\\"error\\"]")).to_contain_text("Username and password do not match")


def test_tc_3(page: Page) -> None:
    page.goto(BASE_URL)
    page.locator("[data-test=\\"login-button\\"]").click()
    expect(page.locator("[data-test=\\"error\\"]")).to_contain_text("Username is required")


TESTS = [("TC-1: Valid login shows Products page", test_tc_1),
         ("TC-2: Wrong password shows error", test_tc_2),
         ("TC-3: Empty fields show required error", test_tc_3)]
'''


def _heal(prompt: str) -> dict:
    """Mock LLM heal: undo the injected '-v2' suffix in whatever script was sent."""
    start = prompt.index("```python\n") + len("```python\n")
    end = prompt.index("\n```", start)
    code = prompt[start:end].replace("-v2", "")
    return {"code": code, "change_summary": "restored the renamed data-test attribute"}


CANNED = {
    "Readiness": {"ready": True, "has_acceptance_criteria": True, "test_type": "ui", "risk_1_to_5": 4, "reason": "mock"},
    "TestPlan": PLAN,
    "Review": {"items": [
        {"id": "TC-1", "automatable": True, "covers_acceptance_criterion": True, "priority": "P1", "duplicate": False},
        {"id": "TC-2", "automatable": True, "covers_acceptance_criterion": True, "priority": "P2", "duplicate": False},
        {"id": "TC-3", "automatable": True, "covers_acceptance_criterion": True, "priority": "P2", "duplicate": False},
    ]},
    "GeneratedScript": {"code": SCRIPT, "notes": "mock"},
    "Triage": {"category": "locator_changed", "should_heal": True, "reason": "mock"},
    "HealedScript": _heal,
    "Report": {"markdown": "# Mock report\n\nAll good."},
}
