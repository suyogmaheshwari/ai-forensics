"""Strip personal and session data from a raw capture before it becomes a fixture.

Kept: path, product headers, body structure, the first 200 characters of each
system/instruction text, tool names and descriptions, parameter habits.
Dropped: credentials, cookies, session and device identifiers, conversation
content, working directories, full prompt texts, tool JSON schemas.
"""
from __future__ import annotations

import re
from typing import Any

DROP_HEADERS = {"authorization", "x-api-key", "cookie", "x-goog-api-key", "api-key", "host",
                "content-length", "connection", "accept-encoding", "openai-organization", "openai-project"}
ID_HEADER_HINTS = ("session", "thread", "request-id", "window", "conversation", "trace", "turn-metadata", "idempotency")
PREFIX_CHARS = 200
_PATH_RX = re.compile(r"(/(?:Users|home|private|tmp|var|opt|root)/[^\s\"'<>]*|[A-Za-z]:\\\\[^\s\"'<>]*)")
_EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_UUID_RX = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def scrub(text: str) -> str:
    """Remove paths, emails and UUIDs from a kept text fragment."""
    text = _PATH_RX.sub("<path>", text)
    text = _EMAIL_RX.sub("<email>", text)
    return _UUID_RX.sub("<uuid>", text)


def _prefix(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    return scrub(text[:PREFIX_CHARS])


def _sanitize_content_list(items: Any) -> list:
    out = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        kept = {"type": item.get("type")}
        if "text" in item:
            kept["text_prefix"] = _prefix(item["text"])
            kept["text_len"] = len(item["text"]) if isinstance(item["text"], str) else 0
        if "cache_control" in item:
            kept["cache_control"] = item["cache_control"]
        out.append(kept)
    return out


def sanitize(record: dict) -> dict:
    headers = {}
    for key, value in (record.get("headers") or {}).items():
        lk = key.lower()
        if lk in DROP_HEADERS:
            continue
        if any(h in lk for h in ID_HEADER_HINTS):
            headers[lk] = "<redacted-id>"
            continue
        headers[lk] = value
    body = record.get("body")
    out = {
        "method": record.get("method"),
        "path": re.sub(r"[0-9a-f]{32}|[0-9a-f-]{36}", "<id>", record.get("path", "")),
        "headers": headers,
        "body": {},
    }
    if not isinstance(body, dict):
        return out
    b: dict[str, Any] = {}
    for key in ("model", "stream", "max_tokens", "max_output_tokens", "temperature", "top_p", "top_k",
                "thinking", "output_config", "store", "include", "reasoning", "text", "tool_choice",
                "parallel_tool_calls", "response_format", "service_tier", "stop", "n", "user"):
        if key in body:
            b[key] = body[key]
    if "prompt_cache_key" in body:
        b["prompt_cache_key"] = "<redacted-id>"
    if "metadata" in body and isinstance(body["metadata"], dict):
        b["metadata_keys"] = sorted(body["metadata"].keys())
    if "client_metadata" in body and isinstance(body["client_metadata"], dict):
        b["client_metadata_keys"] = sorted(body["client_metadata"].keys())
    sys_ = body.get("system")
    if isinstance(sys_, str):
        b["system"] = [{"type": "text", "text_prefix": _prefix(sys_), "text_len": len(sys_)}]
    elif isinstance(sys_, list):
        b["system"] = _sanitize_content_list(sys_)
    if isinstance(body.get("instructions"), str):
        b["instructions"] = {"text_prefix": _prefix(body["instructions"]), "text_len": len(body["instructions"])}
    tools = body.get("tools")
    if isinstance(tools, list):
        b["tools"] = [{"name": t.get("name") or t.get("type"), "type": t.get("type"),
                       "description_prefix": _prefix(t.get("description", ""))} for t in tools if isinstance(t, dict)]
    msgs = body.get("messages")
    if isinstance(msgs, list):
        b["messages"] = []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            entry = {"role": m.get("role")}
            if m.get("role") == "system":
                entry["content"] = _sanitize_content_list(m.get("content")) if isinstance(m.get("content"), list) else [{"type": "text", "text_prefix": _prefix(m.get("content")), "text_len": len(m.get("content") or "")}]
            else:
                entry["content"] = "<redacted>"
            b["messages"].append(entry)
    inp = body.get("input")
    if isinstance(inp, list):
        b["input"] = []
        for item in inp:
            if not isinstance(item, dict):
                continue
            entry = {"type": item.get("type"), "role": item.get("role")}
            if item.get("type") == "additional_tools" and isinstance(item.get("tools"), list):
                entry["tools"] = [{"name": t.get("name") or t.get("type"), "type": t.get("type")} for t in item["tools"] if isinstance(t, dict)]
            elif item.get("role") == "developer":
                entry["content"] = _sanitize_content_list(item.get("content"))
            else:
                entry["content"] = "<redacted>"
            b["input"].append(entry)
    out["body"] = b
    return out
