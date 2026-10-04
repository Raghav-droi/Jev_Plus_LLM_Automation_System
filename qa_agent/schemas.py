"""Pydantic schemas used as structured outputs for the LLM, and as shared data shapes."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TestCase(BaseModel):
    id: str = Field(description="Short id like TC-1")
    title: str
    type: Literal["positive", "negative", "edge"]
    preconditions: list[str]
    steps: list[str]
    expected_result: str
    acceptance_criterion: str = Field(description="The acceptance criterion this case verifies, quoted or paraphrased")


class TestPlan(BaseModel):
    summary: str
    acceptance_criteria: list[str]
    test_cases: list[TestCase]


class Readiness(BaseModel):
    ready: bool
    has_acceptance_criteria: bool
    test_type: Literal["ui", "api", "both"]
    risk_1_to_5: int
    reason: str


class ReviewItem(BaseModel):
    id: str
    automatable: bool
    covers_acceptance_criterion: bool
    priority: Literal["P1", "P2", "P3"]
    duplicate: bool


class Review(BaseModel):
    items: list[ReviewItem]


class GeneratedScript(BaseModel):
    code: str = Field(description="Complete Python source following the runner contract")
    notes: str


class Triage(BaseModel):
    category: Literal["product_bug", "flaky", "locator_changed", "environment", "test_data"]
    should_heal: bool
    reason: str


class HealedScript(BaseModel):
    code: str
    change_summary: str


class Report(BaseModel):
    markdown: str
