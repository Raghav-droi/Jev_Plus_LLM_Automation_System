"""Execute a generated script in a subprocess and return structured results.

Also holds the deterministic helpers both agents share: page hints, fault injection (to simulate a UI change),
failed-locator extraction and template-based locator replacement.

Run as a module:  python -m qa_agent.playwright_runner <script.py> <out.json> <timeout_ms>
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import traceback
from pathlib import Path

CANDIDATE_JS = """
() => Array.from(document.querySelectorAll('input,button,a,select,textarea,[role=button],[data-test]'))
  .slice(0, 60).map(e => ({
    tag: e.tagName.toLowerCase(), id: e.id || null, name: e.getAttribute('name'),
    data_test: e.getAttribute('data-test'), type: e.getAttribute('type'),
    placeholder: e.getAttribute('placeholder'), aria: e.getAttribute('aria-label'),
    text: ((e.innerText || e.value || '') + '').trim().slice(0, 40)
  }))
"""


def selector_for(c: dict) -> str:
    if c.get("data_test"):
        return f'[data-test="{c["data_test"]}"]'
    if c.get("id"):
        return f'#{c["id"]}'
    if c.get("name"):
        return f'{c["tag"]}[name="{c["name"]}"]'
    if c.get("text"):
        return f'{c["tag"]}:has-text("{c["text"]}")'
    return c["tag"]


def describe(c: dict) -> str:
    bits = [c["tag"]]
    for k in ("type", "id", "name", "data_test", "placeholder", "aria", "text"):
        if c.get(k):
            bits.append(f'{k}={c[k]!r}')
    return " ".join(bits)


def collect_candidates(page) -> list[dict]:
    cands = page.evaluate(CANDIDATE_JS)
    for c in cands:
        c["selector"] = selector_for(c)
        c["description"] = describe(c)
    return cands


def page_hints(base_url: str, timeout_ms: int = 15000) -> str:
    """Open the target page once and list its interactive elements, so script generation uses real selectors."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_default_timeout(timeout_ms)
        page.goto(base_url)
        cands = collect_candidates(page)
        browser.close()
    return "\n".join(f"- {c['selector']}  ({c['description']})" for c in cands) or "(no interactive elements found)"


# ---------- fault injection: simulate the UI changing after the script was written ----------
def break_one_selector(source: str) -> tuple[str, str]:
    m = re.search(r'data-test=\\?"([^"\\]+)\\?"', source)
    if m:
        old = m.group(1)
        return source.replace(f'data-test="{old}"', f'data-test="{old}-v2"', 1).replace(
            f'data-test=\\"{old}\\"', f'data-test=\\"{old}-v2\\"', 1), f'data-test "{old}" -> "{old}-v2"'
    m = re.search(r'"#([A-Za-z][\w-]*)"', source)
    if m:
        old = m.group(1)
        return source.replace(f'"#{old}"', f'"#{old}-v2"', 1), f'id "#{old}" -> "#{old}-v2"'
    return source, "no selector found to break"


# ---------- failed-locator extraction and template healing ----------
_LOC_RE = re.compile(r'locator\("((?:[^"\\]|\\.)*)"\)')


def failed_locator(error_text: str) -> str | None:
    m = _LOC_RE.search(error_text)
    if not m:
        return None
    return m.group(1).encode().decode("unicode_escape")


def replace_locator(source: str, old: str, new: str) -> str | None:
    """Replace every string literal that equals `old` (any quoting) with `new`. None if nothing matched."""
    variants = [json.dumps(old), "'" + old.replace("'", "\\'") + "'", '"' + old.replace('"', '\\"') + '"']
    for v in dict.fromkeys(variants):
        if v in source:
            return source.replace(v, json.dumps(new))
    return None


# ---------- subprocess runner ----------
def run_script(script_path: Path, out_path: Path, timeout_ms: int) -> dict:
    cmd = [sys.executable, "-m", "qa_agent.playwright_runner", str(script_path), str(out_path), str(timeout_ms)]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent)
    if not out_path.exists():
        return {"error": "runner crashed", "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-4000:], "results": []}
    data = json.loads(out_path.read_text())
    data["stderr_tail"] = proc.stderr[-1000:]
    return data


def _main(script: str, out: str, timeout_ms: str) -> None:
    from playwright.sync_api import sync_playwright

    spec = importlib.util.spec_from_file_location("generated_tests", script)
    results: list[dict] = []
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        tests = getattr(mod, "TESTS")
    except Exception:
        Path(out).write_text(json.dumps({"import_error": traceback.format_exc(), "results": []}))
        return

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, fn in tests:
            ctx = browser.new_context()
            page = ctx.new_page()
            page.set_default_timeout(int(timeout_ms))
            item = {"name": name, "status": "passed"}
            try:
                fn(page)
            except Exception as e:  # noqa: BLE001 - we want every failure captured
                item["status"] = "failed"
                item["error"] = f"{type(e).__name__}: {e}"[:3000]
                item["failed_locator"] = failed_locator(item["error"])
                try:
                    item["url"] = page.url
                    item["candidates"] = collect_candidates(page)
                except Exception:  # page may be gone
                    item["candidates"] = []
            finally:
                ctx.close()
            results.append(item)
        browser.close()
    Path(out).write_text(json.dumps({"results": results}))


if __name__ == "__main__":
    _main(*sys.argv[1:4])
