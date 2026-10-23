"""Turn a request (raw capture or sanitised fixture) into a flat feature view.

Three layers:
  1. protocol family from the path     -> "messages" | "responses" | "chat_completions" | "other"
  2. headers (lower-cased)             -> {name: value}
  3. body: identity text prefixes, tool names, parameter habits
"""
from __future__ import annotations

import re
from typing import Any

FAMILIES = (
    (re.compile(r"/messages(\?|$)"), "messages"),
    (re.compile(r"/responses(\?|$)"), "responses"),
    (re.compile(r"/chat/completions(\?|$)"), "chat_completions"),
    (re.compile(r"/completions(\?|$)"), "completions"),
    (re.compile(r":generateContent|:streamGenerateContent"), "gemini"),
)


def family_of(path: str) -> str:
    for rx, name in FAMILIES:
        if rx.search(path or ""):
            return name
    return "other"


def _texts(blocks: Any) -> list:
    """Identity texts from a system/instructions/developer value, raw or sanitised."""
    out = []
    if isinstance(blocks, str):
        out.append(blocks)
    elif isinstance(blocks, dict):
        out.append(blocks.get("text") or blocks.get("text_prefix") or "")
    elif isinstance(blocks, list):
        for b in blocks:
            if isinstance(b, dict):
                out.append(b.get("text") or b.get("text_prefix") or "")
            elif isinstance(b, str):
                out.append(b)
    return [t for t in out if t]


def extract(record: dict) -> dict:
    headers = {k.lower(): v for k, v in (record.get("headers") or {}).items()}
    body = record.get("body") if isinstance(record.get("body"), dict) else {}
    identity: list = []
    tools: list = []

    identity += _texts(body.get("system"))
    identity += _texts(body.get("instructions"))
    for m in body.get("messages") or []:
        if isinstance(m, dict) and m.get("role") in ("system", "developer"):
            identity += _texts(m.get("content"))
    for item in body.get("input") or []:
        if not isinstance(item, dict):
            continue
        if item.get("role") == "developer":
            identity += _texts(item.get("content"))
        if item.get("type") == "additional_tools":
            tools += [t.get("name") or t.get("type") for t in item.get("tools") or [] if isinstance(t, dict)]
    for t in body.get("tools") or []:
        if isinstance(t, dict):
            fn = t.get("function") if isinstance(t.get("function"), dict) else None
            tools.append((fn or t).get("name") or t.get("type"))

    habits = {}
    for key in ("stream", "store", "max_tokens", "max_output_tokens", "temperature", "parallel_tool_calls", "tool_choice", "service_tier"):
        if key in body:
            habits[key] = body[key]
    for key in ("thinking", "output_config", "reasoning", "include", "text", "context_management", "metadata", "metadata_keys", "client_metadata_keys", "prompt_cache_key"):
        if key in body:
            habits[key] = True

    return {
        "family": family_of(record.get("path", "")),
        "path": record.get("path", ""),
        "headers": headers,
        "identity": identity,
        "tools": sorted({t for t in tools if t}),
        "habits": habits,
        "model": body.get("model"),
    }
