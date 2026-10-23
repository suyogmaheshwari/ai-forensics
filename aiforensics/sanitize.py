"""Strip personal and session data from a raw capture before it becomes a fixture.

Kept: path, product headers, body structure, the first 200 characters of each
system/instruction text, tool names and descriptions, parameter habits, and the
shape of the conversation (roles, block types, names of tools called).
Dropped: credentials, cookies, session and device identifiers, conversation
content, working directories, full prompt texts, tool JSON schemas.
"""
from __future__ import annotations

import re
from typing import Any

from .reply import turn_shape

DROP_HEADERS = {"authorization", "x-api-key", "cookie", "x-goog-api-key", "api-key", "host",
                "content-length", "connection", "accept-encoding", "openai-organization", "openai-project"}
ID_HEADER_HINTS = ("session", "thread", "request-id", "window", "conversation", "trace", "turn-metadata", "idempotency",
                   "x-client-request", "agent-session", "affinity")
PREFIX_CHARS = 200
_PATH_RX = re.compile(r"(/(?:Users|home|private|tmp|var|opt|root)/[^\s\"'<>]*|[A-Za-z]:\\\\[^\s\"'<>]*)")
_EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_UUID_RX = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_TIMESTAMP_RX = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
KEPT_PARAMS = ("model", "stream", "stream_options", "max_tokens", "max_output_tokens", "max_completion_tokens", "temperature",
               "top_p", "top_k", "thinking", "output_config", "store", "include", "reasoning", "text", "tool_choice",
               "parallel_tool_calls", "response_format", "service_tier", "stop", "n", "user", "context_management", "search_parameters")
GENERATION_CONFIG_KEYS = ("temperature", "topP", "topK", "maxOutputTokens", "thinkingConfig", "responseMimeType", "candidateCount")


def scrub(text: str) -> str:
    """Remove paths, emails and UUIDs from a kept text fragment."""
    text = _PATH_RX.sub("<path>", text)
    text = _EMAIL_RX.sub("<email>", text)
    return _UUID_RX.sub("<uuid>", text)


def _prefix(text: Any) -> str:
    """Scrubbed prefix of a prompt text; timestamps go too, dates alone (feature flags carry them) stay."""
    if not isinstance(text, str):
        return ""
    return _TIMESTAMP_RX.sub("<timestamp>", scrub(text[:PREFIX_CHARS]))


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


def _block_types(content: Any) -> list:
    """Block types of a non-system message, without their content."""
    if isinstance(content, str):
        return ["text"]
    types = []
    for b in content if isinstance(content, list) else []:
        if isinstance(b, dict):
            types.append(b.get("type") or next((k for k in ("text", "functionCall", "functionResponse", "inlineData") if k in b), "?"))
    return types


def _tool_names(tools: Any) -> list:
    out = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "namespace":
            out.append({"name": t.get("name"), "type": "namespace", "tools": _tool_names(t.get("tools"))})
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else None
        src = fn or t
        out.append({"name": src.get("name") or t.get("type"), "type": t.get("type"),
                    "description_prefix": _prefix(src.get("description", ""))})
    return out


def _calls(blocks: Any, key: str) -> list:
    return [b[key].get("name") for b in blocks if isinstance(b, dict) and isinstance(b.get(key), dict)] if isinstance(blocks, list) else []


def sanitize(record: dict) -> dict:
    headers = {}
    for key, value in (record.get("headers") or {}).items():
        lk = key.lower()
        if lk in DROP_HEADERS:
            continue
        if any(h in lk for h in ID_HEADER_HINTS):
            headers[lk] = "<redacted-id>"
            continue
        headers[lk] = scrub(str(value))
    body = record.get("body")
    out = {
        "method": record.get("method"),
        "path": re.sub(r"[0-9a-f]{32}|[0-9a-f-]{36}", "<id>", record.get("path", "")),
        "headers": headers,
        "body": {},
    }
    if not isinstance(body, dict):
        return out
    out["turn"] = turn_shape(record)
    if isinstance(record.get("reply"), dict):
        out["recorder_reply"] = record["reply"].get("kind")
    b: dict[str, Any] = {}
    for key in KEPT_PARAMS:
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
    si = body.get("systemInstruction")
    if isinstance(si, dict):
        b["systemInstruction"] = {"role": si.get("role"), "parts": _sanitize_content_list(si.get("parts"))}
    elif isinstance(si, str):
        b["systemInstruction"] = {"parts": [{"text_prefix": _prefix(si), "text_len": len(si)}]}
    if isinstance(body.get("instructions"), str):
        b["instructions"] = {"text_prefix": _prefix(body["instructions"]), "text_len": len(body["instructions"])}
    gc = body.get("generationConfig")
    if isinstance(gc, dict):
        b["generationConfig"] = {k: gc[k] for k in GENERATION_CONFIG_KEYS if k in gc}
        if "responseJsonSchema" in gc or "responseSchema" in gc:
            b["generationConfig"]["responseSchema_keys"] = sorted(((gc.get("responseJsonSchema") or gc.get("responseSchema") or {}).get("properties") or {}).keys())
    tools = body.get("tools")
    if isinstance(tools, list):
        if tools and isinstance(tools[0], dict) and "functionDeclarations" in tools[0]:
            b["tools"] = [{"functionDeclarations": _tool_names(t.get("functionDeclarations"))} if "functionDeclarations" in t
                          else {"type": next(iter(t.keys()), None)} for t in tools if isinstance(t, dict)]
        else:
            b["tools"] = _tool_names(tools)
    msgs = body.get("messages")
    if isinstance(msgs, list):
        b["messages"] = []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            entry = {"role": m.get("role")}
            if m.get("role") in ("system", "developer"):
                entry["content"] = _sanitize_content_list(m.get("content")) if isinstance(m.get("content"), list) else [{"type": "text", "text_prefix": _prefix(m.get("content")), "text_len": len(m.get("content") or "")}]
            else:
                entry["content"] = "<redacted>"
                entry["content_types"] = _block_types(m.get("content"))
                if m.get("tool_calls"):
                    entry["tool_calls"] = [(tc.get("function") or {}).get("name") for tc in m["tool_calls"] if isinstance(tc, dict)]
                names = [blk.get("name") for blk in m.get("content") or [] if isinstance(blk, dict) and blk.get("type") == "tool_use"] if isinstance(m.get("content"), list) else []
                if names:
                    entry["tool_calls"] = names
                if m.get("role") == "tool" and m.get("name"):
                    entry["name"] = m["name"]
            b["messages"].append(entry)
    inp = body.get("input")
    if isinstance(inp, list):
        b["input"] = []
        for item in inp:
            if not isinstance(item, dict):
                continue
            entry = {"type": item.get("type"), "role": item.get("role")}
            if item.get("type") == "additional_tools" and isinstance(item.get("tools"), list):
                entry["tools"] = _tool_names(item["tools"])
            elif item.get("role") in ("developer", "system"):
                content = item.get("content")
                entry["content"] = _sanitize_content_list(content) if isinstance(content, list) else [{"type": "text", "text_prefix": _prefix(content), "text_len": len(content or "")}]
            elif item.get("type") in ("function_call", "custom_tool_call"):
                entry["name"] = item.get("name")
            else:
                entry["content"] = "<redacted>"
                if item.get("content") is not None:
                    entry["content_types"] = _block_types(item.get("content"))
            b["input"].append(entry)
    contents = body.get("contents")
    if isinstance(contents, list):
        b["contents"] = []
        for c in contents:
            if not isinstance(c, dict):
                continue
            entry = {"role": c.get("role"), "parts": _block_types(c.get("parts"))}
            calls = _calls(c.get("parts"), "functionCall")
            if calls:
                entry["tool_calls"] = calls
            b["contents"].append(entry)
    out["body"] = b
    return out
