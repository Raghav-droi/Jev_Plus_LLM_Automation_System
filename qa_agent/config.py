"""Environment, model and pricing configuration shared by both agents."""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# USD per 1M tokens. Claude prices from the Anthropic price list (2026-09).
# Jev price from TypeSafe's launch post: $0.042 / MTok input, output free.
PRICING = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_read": 0.10, "cache_write": 1.25},
    "jev": {"input": 0.042, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
}


@dataclass(frozen=True)
class Settings:
    llm_model: str = os.getenv("LLM_MODEL", "claude-opus-5-5")
    llm_effort: str = os.getenv("LLM_EFFORT", "medium")
    jev_confidence: float = float(os.getenv("JEV_CONFIDENCE", "0.75"))
    base_url: str = os.getenv("BASE_URL", "https://www.saucedemo.com")
    jira_url: str = os.getenv("JIRA_URL", "")
    jira_username: str = os.getenv("JIRA_USERNAME", "")
    jira_api_token: str = os.getenv("JIRA_API_TOKEN", "")
    mcp_atlassian_cmd: tuple[str, ...] = tuple(shlex.split(os.getenv("MCP_ATLASSIAN_CMD", "mcp-atlassian")))
    max_heal_attempts: int = int(os.getenv("MAX_HEAL_ATTEMPTS", "2"))
    test_timeout_ms: int = int(os.getenv("TEST_TIMEOUT_MS", "8000"))


SETTINGS = Settings()


def price_for(model: str) -> dict[str, float]:
    if model in PRICING:
        return PRICING[model]
    raise KeyError(f"No pricing known for model {model!r}; add it to PRICING in qa_agent/config.py")
