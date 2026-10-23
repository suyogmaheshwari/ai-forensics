import glob
import json
import os

from aiforensics.rules import load_rules
from aiforensics.scorer import identify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = load_rules(os.path.join(ROOT, "rules"))


def test_fixtures_identify_as_their_label():
    paths = glob.glob(os.path.join(ROOT, "fixtures", "*", "*.json"))
    assert paths, "no fixtures found"
    for path in paths:
        label = os.path.basename(os.path.dirname(path))
        with open(path, encoding="utf-8") as fh:
            res = identify(json.load(fh), RULES)
        assert res["decision"] == "identified", (path, res["decision"])
        assert res["product"] == label, (path, res["product"])


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
