"""Turn a request (raw capture or sanitised fixture) into a flat feature view.

Three layers:
  1. protocol family from the path     -> "messages" | "responses" | "chat_completions" | "gemini" | "other"
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
_MODEL_IN_PATH = re.compile(r"/models/([^/:]+):")


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
        if isinstance(blocks.get("parts"), list):
            return _texts(blocks["parts"])
        out.append(blocks.get("text") or blocks.get("text_prefix") or "")
    elif isinstance(blocks, list):
        for b in blocks:
            if isinstance(b, dict):
                out.append(b.get("text") or b.get("text_prefix") or "")
            elif isinstance(b, str):
                out.append(b)
    return [t for t in out if t]


def tool_names(tools: Any) -> list:
    """Tool names from any family's tool list, descending into namespaces and Gemini declarations."""
    out: list = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "namespace" or isinstance(t.get("functionDeclarations"), list):
            out += tool_names(t.get("tools") or t.get("functionDeclarations"))
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else None
        out.append((fn or t).get("name") or t.get("type"))
    return [n for n in out if n]


def extract(record: dict) -> dict:
    headers = {k.lower(): v for k, v in (record.get("headers") or {}).items()}
    body = record.get("body") if isinstance(record.get("body"), dict) else {}
    path = record.get("path", "")
    identity: list = []
    tools: list = []

    identity += _texts(body.get("system"))
    identity += _texts(body.get("instructions"))
    identity += _texts(body.get("systemInstruction"))
    for m in body.get("messages") or []:
        if isinstance(m, dict) and m.get("role") in ("system", "developer"):
            identity += _texts(m.get("content"))
    for item in body.get("input") or []:
        if not isinstance(item, dict):
            continue
        if item.get("role") in ("developer", "system") and item.get("type") != "additional_tools":
            identity += _texts(item.get("content"))
        if item.get("type") == "additional_tools":
            tools += tool_names(item.get("tools"))
    tools += tool_names(body.get("tools"))

    habits = {}
    for key in ("stream", "store", "max_tokens", "max_output_tokens", "max_completion_tokens", "temperature",
                "parallel_tool_calls", "tool_choice", "service_tier"):
        if key in body:
            habits[key] = body[key]
    for key in ("thinking", "output_config", "reasoning", "include", "text", "context_management", "metadata", "metadata_keys",
                "client_metadata_keys", "prompt_cache_key", "stream_options", "generationConfig"):
        if key in body:
            habits[key] = True
    gc = body.get("generationConfig")
    if isinstance(gc, dict):
        for key in ("temperature", "topP", "topK", "thinkingConfig", "responseMimeType"):
            if key in gc:
                habits["generationConfig." + key] = gc[key] if key != "thinkingConfig" else True

    model = body.get("model")
    if not model:
        m = _MODEL_IN_PATH.search(path)
        model = m.group(1) if m else None

    return {
        "family": family_of(path),
        "path": path,
        "headers": headers,
        "identity": identity,
        "tools": sorted({t for t in tools if t}),
        "habits": habits,
        "model": model,
    }
