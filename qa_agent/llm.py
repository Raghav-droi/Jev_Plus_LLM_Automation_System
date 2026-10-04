"""Thin Claude wrapper: structured outputs, usage accounting, and a mock for dry runs."""
from __future__ import annotations

import json
import os
import time
from typing import TypeVar

import anthropic
from pydantic import BaseModel

from .config import SETTINGS
from .metrics import Call, RunMetrics

T = TypeVar("T", bound=BaseModel)


class LLM:
    backend = "llm"

    def __init__(self, metrics: RunMetrics, model: str | None = None, effort: str | None = None):
        self.client = anthropic.Anthropic()
        self.model = model or SETTINGS.llm_model
        self.effort = effort or SETTINGS.llm_effort
        self.metrics = metrics

    def ask(self, step: str, system: str, prompt: str, schema: type[T]) -> T:
        t0 = time.perf_counter()
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
            output_config={"effort": self.effort},
        )
        seconds = time.perf_counter() - t0
        if response.stop_reason == "refusal":
            raise RuntimeError(f"Claude refused step {step}: {response.stop_details}")
        u = response.usage
        self.metrics.record(Call(
            step=step, backend=self.backend, model=self.model, seconds=seconds,
            input_tokens=u.input_tokens, output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_input_tokens or 0,
            cache_write_tokens=u.cache_creation_input_tokens or 0,
        ))
        parsed = response.parsed_output
        if parsed is None:
            raise RuntimeError(f"Claude returned no structured output for step {step} (stop_reason={response.stop_reason})")
        return parsed


class AgentSDKLLM(LLM):
    """Same interface, but routed through the Claude Agent SDK, which reuses the local Claude Code login
    (no ANTHROPIC_API_KEY needed). Each call is a one-turn, no-tools Claude Code session with a JSON schema
    output format. The SDK reports token usage and the API-equivalent cost, which is what we record.

    Note: the SDK spawns a Claude Code process per call, so absolute latency is a few seconds higher than a raw
    API call. Both agents pay the same overhead, so the comparison between them stays fair."""

    def __init__(self, metrics: RunMetrics, model: str | None = None, effort: str | None = None):
        self.model = model or SETTINGS.llm_model
        self.effort = effort or SETTINGS.llm_effort
        self.metrics = metrics

    def ask(self, step: str, system: str, prompt: str, schema: type[T]) -> T:
        import asyncio

        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

        options = ClaudeAgentOptions(
            system_prompt=system, tools=[], allowed_tools=[], max_turns=1, model=self.model,
            effort=self.effort, permission_mode="dontAsk",
            output_format={"type": "json_schema", "schema": schema.model_json_schema()},
            # Run bare: no user settings, CLAUDE.md files or MCP servers from ~/.claude, otherwise every call
            # carries tens of thousands of tokens of unrelated context.
            setting_sources=[], mcp_servers={}, strict_mcp_config=True,
        )

        async def run() -> ResultMessage:
            result: ResultMessage | None = None
            async for message in query(prompt=prompt, options=options):  # drain fully so the generator closes cleanly
                if isinstance(message, ResultMessage):
                    result = message
            if result is None:
                raise RuntimeError(f"Agent SDK returned no result for step {step}")
            return result

        t0 = time.perf_counter()
        result = asyncio.run(run())
        seconds = time.perf_counter() - t0
        if result.is_error:
            raise RuntimeError(f"Agent SDK error in step {step}: {result.subtype} {result.errors or result.result}")
        u = result.usage or {}
        call = Call(step=step, backend=self.backend, model=self.model, seconds=seconds,
                    input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
                    cache_read_tokens=u.get("cache_read_input_tokens", 0) or 0,
                    cache_write_tokens=u.get("cache_creation_input_tokens", 0) or 0, note="agent_sdk")
        self.metrics.record(call)
        if result.total_cost_usd is not None:
            call.cost_usd = result.total_cost_usd  # SDK-reported API-equivalent cost beats our price table
        data = result.structured_output
        if data is None:
            try:
                data = json.loads(result.result or "")
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Agent SDK returned no structured output for step {step}: {result.result!r}") from e
        return schema.model_validate(data)


def make_llm(metrics: RunMetrics) -> LLM:
    """Pick the backend from LLM_BACKEND: 'api' (ANTHROPIC_API_KEY) or 'agent_sdk' (Claude Code login)."""
    backend = os.getenv("LLM_BACKEND", "api").lower()
    if backend == "agent_sdk":
        return AgentSDKLLM(metrics)
    if backend == "api":
        return LLM(metrics)
    raise ValueError(f"LLM_BACKEND must be 'api' or 'agent_sdk', got {backend!r}")


class MockLLM(LLM):
    """Canned answers so the whole pipeline can be exercised without an API key (`--mock`)."""

    def __init__(self, metrics: RunMetrics, canned: dict[str, dict]):
        self.model = "claude-opus-5-5"
        self.effort = "medium"
        self.metrics = metrics
        self.canned = canned

    def ask(self, step: str, system: str, prompt: str, schema: type[T]) -> T:
        time.sleep(0.05)
        key = schema.__name__
        if key not in self.canned:
            raise KeyError(f"MockLLM has no canned answer for {key}")
        data = self.canned[key]
        if callable(data):
            data = data(prompt)
        approx_in = len(system + prompt) // 4
        approx_out = len(json.dumps(data)) // 4
        self.metrics.record(Call(step=step, backend=self.backend, model=self.model, seconds=1.5,
                                 input_tokens=approx_in, output_tokens=approx_out, note="mock"))
        return schema.model_validate(data)
