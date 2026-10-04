# Jev vs LLM: QA agent demo

Two agents do the same job on the same Jira story:

1. read the ticket from Jira (through the `mcp-atlassian` MCP server),
2. analyse the requirement and write test cases,
3. generate a Playwright script,
4. run it against a dummy site (saucedemo.com by default),
5. if a test fails: triage, heal the script, rerun,
6. write a report.

**Agent A (`llm_only`)** uses Claude for every step.
**Agent B (`hybrid_jev`)** uses Jev (TypeSafe AI) for every *decision* and Claude only for *writing*.
Both record per-step latency, tokens and cost so you can compare them.

| Step | llm_only | hybrid_jev |
|---|---|---|
| Fetch ticket | MCP (no AI) | MCP (no AI) |
| Is the story ready? type? risk? | Claude | Jev: 2 Noul + 1 Choice + 1 Score, one call |
| Write test cases | Claude | Claude |
| Review cases (automatable, covers AC, priority, duplicate) | Claude | Jev: 4 questions per case, one call |
| Generate Playwright script | Claude | Claude |
| Run tests | Playwright | Playwright |
| Triage a failure | Claude | Jev: Choice + Noul |
| Heal a broken locator | Claude rewrites the file | Jev picks the replacement element, code patches the selector; Claude only if Jev is below the confidence gate |
| Report | Claude | Jev scores severity, a template writes the prose |

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env   # then fill in the keys
```

Keys you need:

- Claude, one of two ways (`LLM_BACKEND` in `.env`):
  - `agent_sdk` (default): no key. The Claude Agent SDK reuses the login of the `claude` CLI on your machine.
    Run `claude auth status` to confirm you are logged in (`claude auth login` if not). Fine for a personal demo; it
    is not meant for apps you give to other people. Each call spawns a bare Claude Code process (no user settings,
    no MCP servers), which adds roughly 1.7k tokens and 4 to 6 seconds per call, equally for both agents. The cost
    recorded is the API-equivalent cost the SDK reports, even though your subscription is paying.
  - `api`: `ANTHROPIC_API_KEY` from https://console.anthropic.com, direct Messages API.
- `TYPESAFE_API_KEY` from TypeSafe AI early access (https://typesafe.ai). The SDK is `typesafe-sdk`.
- Jira: `JIRA_URL`, `JIRA_USERNAME` (your Atlassian email), `JIRA_API_TOKEN`.

## Connecting Jira through MCP

The agents do not talk to the Jira REST API directly. They start the open-source `mcp-atlassian` MCP server as a
subprocess (stdio transport) and call its `jira_get_issue` tool. See `qa_agent/jira_mcp.py`.

1. Create an API token: Atlassian account -> Security -> API tokens -> Create API token.
2. Put the three `JIRA_*` values in `.env`. The server is started with `READ_ONLY_MODE=true` and only
   `jira_get_issue,jira_search` enabled, so the demo can never modify your Jira.
3. Test the connection:

```bash
python -c "from qa_agent.jira_mcp import fetch_ticket; import json; print(json.dumps(fetch_ticket('DEMO-1'), indent=2))"
```

If you also want the same server inside Claude Desktop or Claude Code, this is the equivalent config:

```json
{
  "mcpServers": {
    "jira": {
      "command": "mcp-atlassian",
      "env": {
        "JIRA_URL": "https://your-site.atlassian.net",
        "JIRA_USERNAME": "you@example.com",
        "JIRA_API_TOKEN": "...",
        "READ_ONLY_MODE": "true"
      }
    }
  }
}
```

## The demo ticket

Create a Story in your demo Jira project and paste this. The text lives in `samples/sample_ticket.json` too, so you
can run offline with `--ticket-file`.

```
Summary: Login: valid user can sign in and invalid password is rejected

As a registered user I want to log in on https://www.saucedemo.com so that I can see the product catalogue.

Test data
- Valid user: standard_user / secret_sauce
- Locked user: locked_out_user / secret_sauce

Acceptance Criteria
- Given I am on the login page, when I enter a valid username and password and click Login, then I land on the Products page (URL contains /inventory.html and the page title text is "Products").
- Given I am on the login page, when I enter a valid username and a wrong password and click Login, then I stay on the login page and an error message containing "Username and password do not match" is shown.
- Given I am on the login page, when I click Login with both fields empty, then an error message containing "Username is required" is shown.
- Given I log in as the locked user, then an error containing "locked out" is shown.
```

## Running

```bash
# smoke test with no API keys and no cost (canned answers, real Playwright run)
python run_compare.py --ticket-file samples/sample_ticket.json --fault --mock

# real run from Jira, both agents, with one selector deliberately broken after generation
python run_compare.py --ticket DEMO-1 --fault

# real run, no fault (tests should simply pass; healing never triggers)
python run_compare.py --ticket DEMO-1

# one agent only
python run_compare.py --ticket DEMO-1 --fault --agent hybrid
```

`--fault` renames the first `data-test` (or `#id`) selector in the generated script to `...-v2` before the first run.
This simulates the UI changing after the test was written, which is what makes the triage and healing steps run.
Without it a correct script passes on run 1 and the comparison only covers readiness, review, generation and report.

## Reading the results

Every run writes `results/<timestamp>/`:

- `comparison.md` and `comparison.json`: the side-by-side table (wall time, cost, calls per backend, who answered each decision step, per-step time, healing history).
- `<agent>/`: `ticket.json`, `readiness.json`, `test_plan.json`, `review.json`, `script_v1.py`, `run_1.json`, `script_v2.py`, `run_2.json`, ..., `report.md`, `metrics.json`, `log.txt`.

Cost is computed from reported token usage and the price table in `qa_agent/config.py`
(Claude list prices; Jev at $0.042 per million input tokens, output free). Mock runs use estimated token counts,
so their cost numbers are illustrative only. Record real numbers from non-mock runs.

## Making the comparison fair

- Both agents use the same model and effort for the writing steps (`LLM_MODEL`, `LLM_EFFORT` in `.env`).
- Both get the same ticket, the same page hints, the same fault, the same timeouts and the same heal budget.
- Test case and script generation are LLM calls in both, so they vary run to run. Run each configuration 3 to 5 times
  and compare medians, not a single run.
- The interesting numbers are the decision steps: readiness, review, triage, locator_match, report. Those are where
  Jev replaces Claude.
- `JEV_CONFIDENCE` (default 0.75) is the gate for template healing. Lower it and more heals skip the LLM; raise it and
  more escalate. Log how often Jev's pick was actually right before trusting a low gate.

## Where the code is

- `qa_agent/base_agent.py`: the pipeline. Decision steps are abstract methods.
- `qa_agent/agent_llm_only.py`: Claude for every decision.
- `qa_agent/agent_hybrid.py`: Jev for every decision, with the confidence gate and LLM fallback.
- `qa_agent/playwright_runner.py`: subprocess runner, candidate element extraction on failure, fault injection,
  failed-locator parsing and template locator replacement.
- `qa_agent/llm.py`, `qa_agent/jev.py`: thin clients that record latency, tokens and cost. Each has a mock.
- `qa_agent/jira_mcp.py`: MCP client for `mcp-atlassian`.
- `qa_agent/prompts.py`, `qa_agent/schemas.py`: shared prompts and structured-output schemas.

## Known limits

- Jev is early access; the SDK (`typesafe-sdk`) may change. The wrapper in `qa_agent/jev.py` is the only place to touch.
- Template healing only handles `page.locator("<css>")` failures. Anything else escalates to Claude.
- Playwright runs headless Chromium. The default per-action timeout is 8 s (`TEST_TIMEOUT_MS`).
