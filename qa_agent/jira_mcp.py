"""Fetch a Jira issue through the mcp-atlassian MCP server (stdio transport).

Both agents call `fetch_ticket(key)`. It starts the MCP server as a subprocess with the JIRA_* env vars,
lists tools, calls `jira_get_issue`, and normalises the result into a small dict.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import SETTINGS


def _server_params() -> StdioServerParameters:
    missing = [k for k in ("JIRA_URL", "JIRA_USERNAME", "JIRA_API_TOKEN") if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"Jira MCP needs these env vars: {', '.join(missing)} (see .env.example)")
    env = {**os.environ, "READ_ONLY_MODE": "true", "ENABLED_TOOLS": "jira_get_issue,jira_search"}
    cmd, *args = SETTINGS.mcp_atlassian_cmd
    return StdioServerParameters(command=cmd, args=args, env=env)


async def _fetch(issue_key: str) -> dict:
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            tool = next((n for n in names if n.endswith("get_issue")), None)
            if tool is None:
                raise RuntimeError(f"Jira MCP server exposes no get_issue tool. Tools: {names}")
            result = await session.call_tool(tool, {"issue_key": issue_key, "fields": "summary,description,issuetype,priority,labels,status"})
            if result.isError:
                raise RuntimeError(f"jira_get_issue failed: {result.content}")
            text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}


def normalise(raw: dict, key: str) -> dict:
    """Reduce the MCP payload to the fields the agents need."""
    fields = raw.get("fields", raw)
    desc = fields.get("description") or raw.get("description") or ""
    if isinstance(desc, dict):  # Atlassian document format: flatten text nodes
        desc = _adf_text(desc)
    return {
        "key": raw.get("key", key),
        "summary": fields.get("summary") or raw.get("summary", ""),
        "type": (fields.get("issuetype") or {}).get("name") if isinstance(fields.get("issuetype"), dict) else fields.get("issue_type", ""),
        "priority": (fields.get("priority") or {}).get("name") if isinstance(fields.get("priority"), dict) else fields.get("priority", ""),
        "status": (fields.get("status") or {}).get("name") if isinstance(fields.get("status"), dict) else fields.get("status", ""),
        "labels": fields.get("labels") or [],
        "description": desc,
    }


def _adf_text(node) -> str:
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        parts = [_adf_text(c) for c in node.get("content", [])]
        sep = "\n" if node.get("type") in {"paragraph", "listItem", "heading"} else ""
        return sep.join(p for p in parts if p)
    if isinstance(node, list):
        return "\n".join(_adf_text(n) for n in node)
    return ""


def fetch_ticket(issue_key: str) -> dict:
    raw = asyncio.run(_fetch(issue_key))
    return normalise(raw, issue_key)


def load_ticket_file(path: str | Path) -> dict:
    """Offline alternative: a JSON file with the same shape `normalise` produces (see samples/)."""
    data = json.loads(Path(path).read_text())
    return normalise(data, data.get("key", Path(path).stem))


def acceptance_criteria_from_text(description: str) -> list[str]:
    """Best-effort extraction of bullet lines under an 'Acceptance Criteria' heading (used for page hints only)."""
    m = re.search(r"acceptance criteria(.*)", description, re.I | re.S)
    block = m.group(1) if m else description
    return [ln.strip(" -*•\t") for ln in block.splitlines() if ln.strip().startswith(("-", "*", "•"))]
