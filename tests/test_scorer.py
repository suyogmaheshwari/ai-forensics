import glob
import json
import os

from aiforensics.cli import main
from aiforensics.rules import load_rules
from aiforensics.scorer import identify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = load_rules(os.path.join(ROOT, "rules"))
FIXTURES = sorted(glob.glob(os.path.join(ROOT, "fixtures", "*", "*.json")))


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_fixtures_identify_as_their_label():
    assert FIXTURES, "no fixtures found"
    for path in FIXTURES:
        label = os.path.basename(os.path.dirname(path))
        res = identify(_load(path), RULES)
        assert res["decision"] == "identified", (path, res["decision"])
        assert res["product"] == label, (path, res["product"])


def test_eval_is_perfect_over_all_fixtures(capsys):
    assert main(["eval"]) == 0
    out = capsys.readouterr().out
    assert "misclassified: 0" in out
    for line in out.splitlines()[1:]:
        cells = line.split()
        if len(cells) == 6 and cells[0] in {r["product"] for r in RULES}:
            assert cells[4] == "1.00" and cells[5] == "1.00", line


def test_every_product_has_fixtures_for_each_session_shape():
    shapes = {}
    for path in FIXTURES:
        label = os.path.basename(os.path.dirname(path))
        shapes.setdefault(label, set()).add(_load(path).get("turn"))
    for rule in RULES:
        got = shapes.get(rule["product"], set())
        if rule["product"] == "aider":   # no tools; its canned priming exchange makes every turn a follow-up
            assert got == {"followup"}, got
            continue
        assert {"first-turn", "after-tool-call", "followup"} <= got, (rule["product"], got)


def test_rules_follow_the_conventions():
    products = [r["product"] for r in RULES]
    assert len(products) == len(set(products))
    for rule in RULES:
        groups = {f["group"] for f in rule["features"]}
        assert len(groups) >= 2, rule["product"]
        assert rule.get("min_groups", 1) >= 2, rule["product"]
        assert rule.get("deny"), ("a deny feature is required where another product shares a layer", rule["product"])
        assert rule.get("notes"), ("record which features vary between turns or versions", rule["product"])
        ids = [f["id"] for f in rule["features"]]
        assert len(ids) == len(set(ids)), rule["product"]


def test_fixtures_carry_no_secrets_or_content():
    import re
    bad = re.compile(r"aiforensics-homes|/tmp/|/home/|/Users/|sk-dummy|Bearer |[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    for path in FIXTURES:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        assert not bad.search(text), path
        fx = _load(path)
        for key in ("authorization", "x-api-key", "x-goog-api-key", "cookie", "host"):
            assert key not in fx["headers"], (path, key)
        for m in fx["body"].get("messages", []) + fx["body"].get("input", []):
            if m.get("role") not in ("system", "developer") and "content" in m:
                assert m["content"] == "<redacted>", (path, m)


def test_unknown_request_is_unknown():
    rec = {"method": "POST", "path": "/v1/chat/completions", "headers": {"user-agent": "python-requests/2.31"},
           "body": {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}}
    assert identify(rec, RULES)["decision"] == "unknown"


def test_family_gate_blocks_wrong_protocol():
    rec = {"method": "POST", "path": "/v1/chat/completions", "headers": {"user-agent": "claude-cli/9.9.9"},
           "body": {"model": "x", "system": "You are Claude Code", "tools": [{"name": "Bash"}, {"name": "Read"}]}}
    assert identify(rec, RULES)["product"] is None


def test_deny_feature_vetoes_product():
    rec = {"method": "POST", "path": "/v1/messages", "headers": {"user-agent": "claude-cli/9.9.9", "originator": "codex_exec"},
           "body": {"model": "x", "system": "You are Claude Code", "tools": [{"name": "Bash"}, {"name": "Read"}]}}
    assert identify(rec, RULES)["product"] is None


def test_openclaw_identified_with_and_without_attribution_headers():
    body = {"model": "gpt-4o", "stream": True, "stream_options": {"include_usage": True}, "max_completion_tokens": 8192,
            "messages": [{"role": "system", "content": "You are a personal assistant running inside OpenClaw.\n## Tooling"}, {"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": n, "parameters": {}}} for n in
                      ("exec", "read", "write", "edit", "apply_patch", "web_fetch", "web_search", "memory_search", "memory_get", "sessions_list",
                       "sessions_spawn", "session_status", "cron", "gateway", "message", "process", "nodes", "canvas", "browser", "tts")]}
    proxy = {"method": "POST", "path": "/v1/chat/completions", "headers": {"user-agent": "OpenAI/JS 6.39.1", "traceparent": "00-x-y-01"}, "body": body}
    native = {"method": "POST", "path": "/v1/chat/completions", "body": dict(body, store=False),
              "headers": {"user-agent": "openclaw/2026.6.35", "originator": "openclaw", "version": "2026.6.35", "traceparent": "00-x-y-01"}}
    for rec in (proxy, native):
        res = identify(rec, RULES)
        assert res["decision"] == "identified" and res["product"] == "openclaw", res["scores"][:3]
    headers_only = dict(native, body={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
    assert identify(headers_only, RULES)["product"] is None   # a header alone never carries a verdict


def test_fork_does_not_conflict_with_its_parent():
    """Kilo Code keeps OpenCode's user-agent and originator; its own headers must veto OpenCode."""
    kilo = {"method": "POST", "path": "/v1/responses",
            "headers": {"user-agent": "opencode/7.8.17 ai-sdk/provider-utils/4.0.40 runtime/bun/1.4.4", "originator": "opencode",
                        "x-title": "Kilo Code", "http-referer": "https://kilocode.ai"},
            "body": {"model": "gpt-4o", "store": False, "input": [{"role": "system", "content": "You are Kilo, a highly skilled software engineer"}],
                     "tools": [{"type": "function", "name": n} for n in ("bash", "edit", "read", "glob", "grep", "goal", "link_pr", "board_post", "todowrite", "write")]}}
    res = identify(kilo, RULES)
    assert res["decision"] == "identified" and res["product"] == "kilocode", res["scores"][:3]
    del kilo["headers"]["x-title"], kilo["headers"]["http-referer"]
    kilo["body"]["input"][0]["content"] = "You are opencode, an agent"
    res = identify(kilo, RULES)
    assert res["product"] == "opencode", res["scores"][:3]
