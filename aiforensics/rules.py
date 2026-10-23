"""Rule format (JSON, one file per product under rules/).

{
  "product": "claude_code",
  "display_name": "Claude Code",
  "family": "messages",                 # layer-1 gate; "any" to skip
  "threshold": 60,
  "min_groups": 2,                      # distinct feature groups that must match
  "group_caps": {"header": 50, "identity": 40, "tools": 40, "habits": 20},
  "features": [
    {"id": "ua", "group": "header", "op": "header_prefix", "name": "user-agent", "value": "claude-cli/", "weight": 30},
    {"id": "beta", "group": "header", "op": "header_contains", "name": "anthropic-beta", "value": "claude-code-", "weight": 20},
    {"id": "identity", "group": "identity", "op": "identity_prefix", "value": ["You are Claude Code", "You are a Claude agent"], "weight": 30},
    {"id": "tools", "group": "tools", "op": "tool_set", "value": ["Bash", "Read", ...], "weight": 30},
    {"id": "thinking", "group": "habits", "op": "habit_present", "value": "thinking", "weight": 10}
  ],
  "deny": [ {"op": "header_prefix", "name": "originator", "value": "codex"} ]
}

Operators: header_equals, header_prefix, header_contains, header_present,
identity_prefix (any of the listed prefixes, case-sensitive, leading whitespace ignored),
identity_contains, tool_set (share of the known tool names present in the request: >=0.7 full
weight, >=0.4 half, else 0), tool_present (any of the listed names), habit_present, habit_equals,
model_prefix.
"""
from __future__ import annotations

import json
import os
from typing import Any


def load_rules(rules_dir: str) -> list:
    rules = []
    for name in sorted(os.listdir(rules_dir)):
        if name.endswith(".json"):
            with open(os.path.join(rules_dir, name), encoding="utf-8") as fh:
                rules.append(json.load(fh))
    return rules


def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else [v]


def feature_matches(feature: dict, fx: dict):
    """Return (matched: bool, strength: float in 0..1)."""
    op = feature["op"]
    value = feature.get("value")
    if op.startswith("header_"):
        hv = fx["headers"].get(feature.get("name", "").lower())
        if hv is None:
            return False, 0.0
        hv = str(hv)
        if op == "header_present":
            return True, 1.0
        if op == "header_equals":
            return any(hv == v for v in _as_list(value)), 1.0
        if op == "header_prefix":
            return any(hv.startswith(v) for v in _as_list(value)), 1.0
        if op == "header_contains":
            return any(v in hv for v in _as_list(value)), 1.0
    if op in ("identity_prefix", "identity_contains"):
        for text in fx["identity"]:
            t = text.lstrip()
            for v in _as_list(value):
                if (op == "identity_prefix" and t.startswith(v)) or (op == "identity_contains" and v in t):
                    return True, 1.0
        return False, 0.0
    if op == "tool_set":
        known = set(_as_list(value))
        seen = set(fx["tools"])
        if not known or not seen:
            return False, 0.0
        # Share of the product's known tool names present in the request. Users add
        # MCP and plugin tools, so the request set is usually a superset; Jaccard would
        # punish that, containment does not.
        coverage = len(known & seen) / len(known)
        if coverage >= 0.7:
            return True, 1.0
        if coverage >= 0.4:
            return True, 0.5
        return False, 0.0
    if op == "tool_present":
        return any(v in fx["tools"] for v in _as_list(value)), 1.0
    if op == "habit_present":
        return any(v in fx["habits"] for v in _as_list(value)), 1.0
    if op == "habit_equals":
        return fx["habits"].get(feature.get("name")) == value, 1.0
    if op == "model_prefix":
        m = fx.get("model") or ""
        return any(m.startswith(v) for v in _as_list(value)), 1.0
    raise ValueError(f"unknown operator {op!r} in feature {feature.get('id')!r}")
