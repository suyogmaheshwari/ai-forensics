"""Recorder reply mode: answer a captured request with a minimal valid response.

`tool-call` mode returns one harmless tool call (a shell `echo ok`, or a read of
README.md when the agent has no shell tool) built from the tool schemas the
request itself declares, then a plain text answer once the request carries the
tool result. Every protocol family is covered: /v1/messages, /v1/chat/completions,
/v1/responses and Gemini :generateContent, as SSE when the request streams and as
JSON otherwise. The decision is stateless: it is read off the last item of the
conversation in the request, so follow-up turns and retries need no bookkeeping.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

from .features import family_of

SHELL_TOOLS = ("bash", "shell", "exec", "exec_command", "run_shell_command", "run_commands", "execute_command",
               "shell_command", "run_command", "run_terminal_cmd", "terminal", "developer__shell", "command")
READ_TOOLS = ("read", "read_file", "read_files", "view_file", "view", "cat", "text_editor", "str_replace_based_edit_tool")
DONE_TOOLS = ("submit_and_exit", "attempt_completion", "complete_task", "task_complete", "finish", "done", "submit", "complete")
PATH_PROPS = ("file_path", "filepath", "path", "absolute_path", "file", "target_file", "notebook_path")
DIR_PROPS = ("workdir", "cwd", "directory", "dir_path", "working_directory", "working_dir")
TEXT_REPLY = "ok"
# Names answered by the model listings; agents that validate the configured model against GET /v1/models
# (Forge) or LiteLLM's GET /v1/model/info (Junie, built-in names only) need to find it here.
KNOWN_MODELS = ("aiforensics", "gpt-4o", "gpt-5", "gpt-5.4", "claude-sonnet-4-5", "claude-sonnet-4-6", "gemini-2.5-pro", "probe-model")
_COUNTER = {"n": 0}
_MODEL_IN_PATH = re.compile(r"/models/([^/:]+):")


# ----------------------------------------------------------------- tool discovery

def _schema(tool: dict) -> dict:
    for key in ("input_schema", "parameters", "parametersJsonSchema"):
        if isinstance(tool.get(key), dict):
            return tool[key]
    return {}


def declared_tools(body: dict) -> list:
    """Flat list of {name, type, schema, format} from any protocol family."""
    out = []

    def add(t: Any) -> None:
        if not isinstance(t, dict):
            return
        if t.get("type") == "namespace":
            for sub in t.get("tools") or []:
                add(sub)
            return
        fn = t.get("function") if isinstance(t.get("function"), dict) else None
        if "functionDeclarations" in t:
            for decl in t.get("functionDeclarations") or []:
                add(decl)
            return
        src = fn or t
        name = src.get("name")
        if not name:
            return
        out.append({"name": name, "type": t.get("type") or "function", "schema": _schema(src), "format": src.get("format")})

    for t in body.get("tools") or []:
        add(t)
    for item in body.get("input") or []:
        if isinstance(item, dict) and item.get("type") == "additional_tools":
            for t in item.get("tools") or []:
                add(t)
    return out


def pick_tool(tools: list, prefer_done: bool = False) -> Optional[dict]:
    by_lower = {t["name"].lower(): t for t in tools}
    for group in ([DONE_TOOLS] if prefer_done else [SHELL_TOOLS, READ_TOOLS]):
        for name in group:
            if name in by_lower:
                return by_lower[name]
    if prefer_done:
        return None
    for t in tools:
        if t["type"] == "function":
            return t
    return tools[0] if tools else None


# ----------------------------------------------------------------- argument synthesis

def _types(schema: dict) -> set:
    t = schema.get("type")
    if isinstance(t, list):
        return {str(x).lower() for x in t}
    return {str(t).lower()} if t else set()


def fill_schema(schema: dict, work_dir: str, name: str = "", depth: int = 0) -> Any:
    """A value satisfying a JSON schema, choosing harmless values for well-known properties."""
    if not isinstance(schema, dict) or depth > 4:
        return TEXT_REPLY
    lname = name.lower()
    types = _types(schema)
    if "enum" in schema and schema["enum"]:
        by_lower = {str(v).lower(): v for v in schema["enum"]}
        for want in ("shell", "bash", "sh", "python"):
            if want in by_lower:
                return by_lower[want]
        return schema["enum"][0]
    desc = str(schema.get("description") or "").lower()
    if lname in ("command", "cmd", "commands", "script", "code"):
        if "array" in types:
            argv = "argv" in desc or "arguments" in desc or "program" in desc
            return ["echo", "ok"] if argv else ["echo ok"]
        return "echo ok"
    if lname in PATH_PROPS and "string" in types:
        return work_dir.rstrip("/") + "/README.md" if work_dir else "README.md"
    if lname in DIR_PROPS and "string" in types:
        return work_dir or "."
    if "object" in types or "properties" in schema:
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        out = {}
        for key in required:
            if key in props:
                out[key] = fill_schema(props[key], work_dir, key, depth + 1)
        if not out and props:
            key = next(iter(props))
            out[key] = fill_schema(props[key], work_dir, key, depth + 1)
        return out
    if "array" in types:
        items = schema.get("items")
        if isinstance(items, dict) and ("object" in _types(items) or "properties" in items):
            return [fill_schema(items, work_dir, name, depth + 1)]
        return []
    if "integer" in types or "number" in types:
        return 1
    if "boolean" in types:
        return True
    if lname in ("description", "summary", "reason", "message", "text"):
        return "say ok"
    return TEXT_REPLY


# ----------------------------------------------------------------- conversation shape

def _is_tool_result(item: Any, family: str) -> bool:
    if not isinstance(item, dict):
        return False
    if family == "messages":
        c = item.get("content")
        return item.get("role") == "user" and isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
    if family == "chat_completions":
        return item.get("role") == "tool"
    if family == "responses":
        return item.get("type") in ("function_call_output", "custom_tool_call_output")
    if family == "gemini":
        return any(isinstance(p, dict) and "functionResponse" in p for p in item.get("parts") or [])
    return False


def _is_tool_call(item: Any, family: str) -> bool:
    if not isinstance(item, dict):
        return False
    if family == "messages":
        c = item.get("content")
        return item.get("role") == "assistant" and isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_use" for b in c)
    if family == "chat_completions":
        return item.get("role") == "assistant" and bool(item.get("tool_calls"))
    if family == "responses":
        return item.get("type") in ("function_call", "custom_tool_call")
    if family == "gemini":
        return any(isinstance(p, dict) and "functionCall" in p for p in item.get("parts") or [])
    return False


def has_assistant_text(items: list, family: str) -> bool:
    """True when an assistant turn in the history is a plain text answer, not a tool call."""
    for item in items:
        if not isinstance(item, dict) or _is_tool_call(item, family):
            continue
        if family == "gemini":
            if item.get("role") == "model" and any(isinstance(p, dict) and p.get("text") for p in item.get("parts") or []):
                return True
        elif item.get("role") == "assistant" and item.get("content"):
            return True
    return False


def conversation(body: dict, family: str) -> list:
    if family == "gemini":
        return body.get("contents") or []
    if family == "responses":
        return body.get("input") or []
    return body.get("messages") or []


def _is_assistant(item: Any, family: str) -> bool:
    return isinstance(item, dict) and (item.get("role") in ("assistant", "model") or _is_tool_call(item, family))


def turn_shape(record: dict) -> str:
    """first-turn | after-tool-call | followup, read off the request's conversation.

    Structural, not positional: agents append reminder messages after the tool result
    (Claude Code a system message, Qwen Code a user message), so the question is
    whether a tool result follows the last assistant turn, not what the last item is.
    """
    body = record.get("body") if isinstance(record.get("body"), dict) else {}
    family = family_of(record.get("path", ""))
    items = conversation(body, family)
    last_assistant = max((i for i, item in enumerate(items) if _is_assistant(item, family)), default=-1)
    if any(_is_tool_result(item, family) for item in items[last_assistant + 1:]):
        return "after-tool-call"
    return "followup" if last_assistant >= 0 else "first-turn"


# ----------------------------------------------------------------- response builders

def _sse(events: list) -> bytes:
    out = []
    for name, data in events:
        if name:
            out.append(f"event: {name}\n")
        out.append("data: " + json.dumps(data) + "\n\n")
    return "".join(out).encode("utf-8")


def _messages_reply(model: str, call: Optional[dict], stream: bool):
    if call:
        block = {"type": "tool_use", "id": "toolu_" + call["id"], "name": call["name"], "input": call["args"]}
        stop = "tool_use"
    else:
        block = {"type": "text", "text": TEXT_REPLY}
        stop = "end_turn"
    usage = {"input_tokens": 10, "output_tokens": 10}
    if not stream:
        return json.dumps({"id": "msg_aiforensics", "type": "message", "role": "assistant", "model": model, "content": [block],
                           "stop_reason": stop, "stop_sequence": None, "usage": usage}).encode()
    start = dict(block, input={}) if call else dict(block, text="")
    delta = ({"type": "input_json_delta", "partial_json": json.dumps(call["args"])} if call
             else {"type": "text_delta", "text": TEXT_REPLY})
    return _sse([
        ("message_start", {"type": "message_start", "message": {"id": "msg_aiforensics", "type": "message", "role": "assistant", "model": model,
                                                                 "content": [], "stop_reason": None, "stop_sequence": None,
                                                                 "usage": {"input_tokens": 10, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": start}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 10}}),
        ("message_stop", {"type": "message_stop"}),
    ])


def _chat_reply(model: str, call: Optional[dict], stream: bool, include_usage: bool):
    created = int(time.time())
    usage = {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
    if call:
        tc = {"id": "call_" + call["id"], "type": "function", "function": {"name": call["name"], "arguments": json.dumps(call["args"])}}
        message = {"role": "assistant", "content": None, "tool_calls": [tc]}
        finish = "tool_calls"
    else:
        message = {"role": "assistant", "content": TEXT_REPLY}
        finish = "stop"
    if not stream:
        return json.dumps({"id": "chatcmpl-aiforensics", "object": "chat.completion", "created": created, "model": model,
                           "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage}).encode()

    def chunk(delta: dict, finish_reason=None, **extra):
        d = {"id": "chatcmpl-aiforensics", "object": "chat.completion.chunk", "created": created, "model": model,
             "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}
        d.update(extra)
        return d

    if call:
        chunks = [chunk({"role": "assistant", "content": None,
                         "tool_calls": [{"index": 0, "id": "call_" + call["id"], "type": "function", "function": {"name": call["name"], "arguments": ""}}]}),
                  chunk({"tool_calls": [{"index": 0, "function": {"arguments": json.dumps(call["args"])}}]}),
                  chunk({}, finish)]
    else:
        chunks = [chunk({"role": "assistant", "content": ""}), chunk({"content": TEXT_REPLY}), chunk({}, finish)]
    if include_usage:
        chunks.append({"id": "chatcmpl-aiforensics", "object": "chat.completion.chunk", "created": created, "model": model, "choices": [], "usage": usage})
    body = _sse([(None, c) for c in chunks]).decode()
    return (body + "data: [DONE]\n\n").encode()


def _responses_reply(model: str, call: Optional[dict], stream: bool):
    created = int(time.time())
    usage = {"input_tokens": 10, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 10,
             "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 20}
    if call and call["type"] == "custom":
        item = {"type": "custom_tool_call", "id": "ctc_" + call["id"], "call_id": "call_" + call["id"], "name": call["name"],
                "input": call["args"], "status": "completed"}
    elif call:
        item = {"type": "function_call", "id": "fc_" + call["id"], "call_id": "call_" + call["id"], "name": call["name"],
                "arguments": json.dumps(call["args"]), "status": "completed"}
    else:
        item = {"type": "message", "id": "msg_aiforensics", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": TEXT_REPLY, "annotations": []}]}

    def response(status: str, output: list, with_usage: bool):
        r = {"id": "resp_aiforensics", "object": "response", "created_at": created, "status": status, "model": model, "output": output}
        if with_usage:
            r["usage"] = usage
        return r

    if not stream:
        return json.dumps(response("completed", [item], True)).encode()
    seq = {"n": 0}

    def ev(name: str, **data):
        seq["n"] += 1
        d = {"type": name, "sequence_number": seq["n"]}
        d.update(data)
        return name, d

    events = [ev("response.created", response=response("in_progress", [], False))]
    if call and call["type"] == "custom":
        events += [ev("response.output_item.added", output_index=0, item=dict(item, input="", status="in_progress")),
                   ev("response.custom_tool_call_input.delta", output_index=0, item_id=item["id"], delta=item["input"]),
                   ev("response.custom_tool_call_input.done", output_index=0, item_id=item["id"], input=item["input"])]
    elif call:
        events += [ev("response.output_item.added", output_index=0, item=dict(item, arguments="", status="in_progress")),
                   ev("response.function_call_arguments.delta", output_index=0, item_id=item["id"], delta=item["arguments"]),
                   ev("response.function_call_arguments.done", output_index=0, item_id=item["id"], arguments=item["arguments"])]
    else:
        part = {"type": "output_text", "text": "", "annotations": []}
        events += [ev("response.output_item.added", output_index=0, item=dict(item, content=[], status="in_progress")),
                   ev("response.content_part.added", output_index=0, item_id=item["id"], content_index=0, part=part),
                   ev("response.output_text.delta", output_index=0, item_id=item["id"], content_index=0, delta=TEXT_REPLY),
                   ev("response.output_text.done", output_index=0, item_id=item["id"], content_index=0, text=TEXT_REPLY),
                   ev("response.content_part.done", output_index=0, item_id=item["id"], content_index=0, part=dict(part, text=TEXT_REPLY))]
    events += [ev("response.output_item.done", output_index=0, item=item),
               ev("response.completed", response=response("completed", [item], True))]
    return _sse(events)


def _gemini_reply(model: str, call: Optional[dict], stream: bool, text: str = TEXT_REPLY):
    part = {"functionCall": {"name": call["name"], "args": call["args"]}} if call else {"text": text}
    data = {"candidates": [{"content": {"role": "model", "parts": [part]}, "finishReason": "STOP", "index": 0}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 10, "totalTokenCount": 20},
            "modelVersion": model, "responseId": "aiforensics"}
    return _sse([(None, data)]) if stream else json.dumps(data).encode()


# ----------------------------------------------------------------- entry point

def next_id() -> str:
    """Ids must be unique across a session: agents that resume a transcript drop a tool call whose id they already saw."""
    _COUNTER["n"] += 1
    return f"aiforensics_{int(time.time()) % 100000}_{_COUNTER['n']}"


def build_reply(record: dict, mode: str, work_dir: str = "") -> dict:
    """Return {status, content_type, body, kind} for a captured request."""
    method, path = record.get("method"), record.get("path", "")
    body = record.get("body") if isinstance(record.get("body"), dict) else {}
    family = family_of(path)
    if method == "GET" and path.rstrip("/").endswith("/models"):
        return {"status": 200, "content_type": "application/json", "kind": "models",
                "body": json.dumps({"object": "list", "data": [{"id": m, "object": "model", "owned_by": "aiforensics"} for m in KNOWN_MODELS]}).encode()}
    if method == "GET" and path.rstrip("/").endswith("/model/info"):
        data = [{"model_name": m, "litellm_params": {"model": m}, "model_info": {
            "id": m, "litellm_provider": "anthropic" if m.startswith("claude") else "openai", "max_input_tokens": 200000,
            "max_output_tokens": 32000, "supports_function_calling": True, "supports_vision": True, "mode": "chat"}} for m in KNOWN_MODELS]
        return {"status": 200, "content_type": "application/json", "kind": "models", "body": json.dumps({"data": data}).encode()}
    if method != "POST" or family not in ("messages", "chat_completions", "responses", "gemini"):
        return {"status": 404, "content_type": "application/json", "kind": "unhandled",
                "body": b'{"error":{"type":"not_found_error","message":"aiforensics recorder: no reply for this path"}}'}

    model = body.get("model") or (_MODEL_IN_PATH.search(path).group(1) if _MODEL_IN_PATH.search(path) else "aiforensics")
    stream = bool(body.get("stream")) if family != "gemini" else "streamGenerateContent" in path
    tools = declared_tools(body)
    shape = turn_shape(record)
    call = None
    if mode == "tool-call" and tools:
        if shape != "after-tool-call":
            tool = pick_tool(tools)
        elif has_assistant_text(conversation(body, family), family):
            # the agent already got a text answer and came back (Cline nags until a completion tool is called)
            tool = pick_tool(tools, prefer_done=True)
        else:
            tool = None
        if tool is not None:
            args = "echo ok" if tool["type"] == "custom" else fill_schema(tool["schema"], work_dir)
            call = {"name": tool["name"], "type": tool["type"], "args": args, "id": next_id()}
    kind = "tool-call" if call else "text"

    if family == "messages":
        out = _messages_reply(model, call, stream)
    elif family == "chat_completions":
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        out = _chat_reply(model, call, stream, include_usage)
    elif family == "responses":
        out = _responses_reply(model, call, stream)
    else:
        text = TEXT_REPLY
        gc = body.get("generationConfig") or {}
        if not call and gc.get("responseMimeType") == "application/json":
            schema = gc.get("responseJsonSchema") or gc.get("responseSchema") or {}
            text = json.dumps(fill_schema(schema, work_dir))
        out = _gemini_reply(model, call, stream, text)
    return {"status": 200, "content_type": "text/event-stream" if stream else "application/json", "body": out, "kind": kind}
