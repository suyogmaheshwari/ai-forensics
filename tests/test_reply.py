import json
import os
import socket
import tempfile
import threading
import urllib.request

from aiforensics import recorder
from aiforensics.reply import build_reply, declared_tools, fill_schema, pick_tool, turn_shape

TOOLS_CHAT = [{"type": "function", "function": {"name": "run_shell_command", "parameters": {
    "type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["command"]}}}]
TOOLS_MESSAGES = [{"name": "Read", "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}},
                  {"name": "Bash", "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}]


def _rec(path, body, method="POST"):
    return {"method": method, "path": path, "headers": {}, "body": body}


def _sse_events(raw):
    out = []
    for block in raw.decode().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: ") and line != "data: [DONE]":
                out.append(json.loads(line[6:]))
    return out


# ------------------------------------------------------------------ tool selection and arguments

def test_shell_tool_preferred_over_read_tool():
    tools = declared_tools({"tools": TOOLS_MESSAGES})
    assert pick_tool(tools)["name"] == "Bash"


def test_nested_namespaces_and_gemini_declarations_are_flattened():
    body = {"input": [{"type": "additional_tools", "tools": [{"type": "namespace", "name": "functions", "tools": [
        {"type": "custom", "name": "exec", "format": {"type": "grammar"}}, {"type": "function", "name": "wait", "parameters": {}}]}]}],
        "tools": [{"functionDeclarations": [{"name": "read_file", "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}}}}]}]}
    names = sorted(t["name"] for t in declared_tools(body))
    assert names == ["exec", "read_file", "wait"]
    assert pick_tool(declared_tools(body))["type"] == "custom"


def test_fill_schema_picks_harmless_values():
    schema = {"type": "object", "required": ["command", "file_path", "commands", "n", "flag"], "properties": {
        "command": {"type": "string"}, "file_path": {"type": "string"}, "commands": {"type": "array", "items": {"type": "string"}},
        "n": {"type": "integer"}, "flag": {"type": "boolean"}, "optional": {"type": "string"}}}
    args = fill_schema(schema, "/work")
    assert args == {"command": "echo ok", "file_path": "/work/README.md", "commands": ["echo ok"], "n": 1, "flag": True}


def test_fill_schema_argv_arrays_and_object_items():
    argv = {"type": "object", "required": ["command"], "properties": {"command": {"type": "array", "description": "argv of the program", "items": {"type": "string"}}}}
    assert fill_schema(argv, "") == {"command": ["echo", "ok"]}
    files = {"type": "object", "required": ["files"], "properties": {"files": {"type": "array", "items": {
        "type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}}}}}
    assert fill_schema(files, "") == {"files": [{"path": "README.md"}]}
    assert fill_schema({"type": "OBJECT", "required": ["score"], "properties": {"score": {"type": "INTEGER"}}}, "") == {"score": 1}
    execute = {"type": "object", "required": ["language", "code"], "properties": {"language": {"type": "string", "enum": ["ruby", "python", "shell"]}, "code": {"type": "string"}}}
    assert fill_schema(execute, "") == {"language": "shell", "code": "echo ok"}


# ------------------------------------------------------------------ turn shape

def test_turn_shape_messages_family():
    first = _rec("/v1/messages", {"messages": [{"role": "user", "content": "hi"}]})
    after = _rec("/v1/messages", {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]},
                                               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]},
                                               {"role": "system", "content": [{"type": "text", "text": "reminder"}]}]})
    follow = _rec("/v1/messages", {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}, {"role": "user", "content": "bye"}]})
    assert turn_shape(first) == "first-turn"
    assert turn_shape(after) == "after-tool-call"   # trailing system reminder is skipped
    assert turn_shape(follow) == "followup"
    after["body"]["messages"].append({"role": "user", "content": [{"type": "text", "text": "<system-reminder>memory</system-reminder>"}]})
    assert turn_shape(after) == "after-tool-call"   # Qwen Code style trailing user reminder
    follow["body"]["messages"].append({"role": "system", "content": "reminder"})
    assert turn_shape(follow) == "followup"


def test_turn_shape_other_families():
    chat = _rec("/v1/chat/completions", {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"},
                                                       {"role": "assistant", "tool_calls": [{"id": "c", "function": {"name": "f", "arguments": "{}"}}]},
                                                       {"role": "tool", "tool_call_id": "c", "content": "ok"}]})
    responses = _rec("/v1/responses", {"input": [{"role": "user", "content": "hi"}, {"type": "function_call", "call_id": "c", "name": "f", "arguments": "{}"},
                                                 {"type": "function_call_output", "call_id": "c", "output": "ok"}]})
    gemini = _rec("/v1beta/models/m:generateContent", {"contents": [{"role": "user", "parts": [{"text": "hi"}]},
                                                                      {"role": "model", "parts": [{"functionCall": {"name": "f", "args": {}}}]},
                                                                      {"role": "user", "parts": [{"functionResponse": {"name": "f", "response": {}}}]}]})
    assert turn_shape(chat) == turn_shape(responses) == turn_shape(gemini) == "after-tool-call"
    gemini_follow = _rec("/v1beta/models/m:generateContent", {"contents": [{"role": "user", "parts": [{"text": "hi"}]}, {"role": "model", "parts": [{"text": "ok"}]}, {"role": "user", "parts": [{"text": "bye"}]}]})
    assert turn_shape(gemini_follow) == "followup"


# ------------------------------------------------------------------ reply shapes per family

def test_messages_reply_json_then_text():
    first = _rec("/v1/messages", {"model": "m", "messages": [{"role": "user", "content": "hi"}], "tools": TOOLS_MESSAGES})
    r = build_reply(first, "tool-call", "/w")
    assert r["kind"] == "tool-call" and r["content_type"] == "application/json"
    msg = json.loads(r["body"])
    assert msg["stop_reason"] == "tool_use" and msg["content"][0] == {"type": "tool_use", "id": msg["content"][0]["id"], "name": "Bash", "input": {"command": "echo ok"}}
    after = _rec("/v1/messages", {"model": "m", "messages": first["body"]["messages"] + [
        {"role": "assistant", "content": msg["content"]}, {"role": "user", "content": [{"type": "tool_result", "tool_use_id": msg["content"][0]["id"], "content": "ok"}]}], "tools": TOOLS_MESSAGES})
    r2 = build_reply(after, "tool-call", "/w")
    assert r2["kind"] == "text" and json.loads(r2["body"])["content"] == [{"type": "text", "text": "ok"}]


def test_completion_tool_called_only_after_a_text_answer():
    done = {"type": "function", "function": {"name": "submit_and_exit", "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}}}
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "function": {"name": "run_shell_command", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c", "content": "ok"}]
    after = _rec("/v1/chat/completions", {"model": "m", "messages": msgs, "tools": TOOLS_CHAT + [done]})
    assert build_reply(after, "tool-call", "")["kind"] == "text"
    nag = msgs + [{"role": "assistant", "content": "ok"}, {"role": "user", "content": "[SYSTEM] call submit_and_exit"}]
    r = build_reply(_rec("/v1/chat/completions", {"model": "m", "messages": nag, "tools": TOOLS_CHAT + [done]}), "tool-call", "")
    assert json.loads(r["body"])["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "run_shell_command"
    again = nag + [{"role": "assistant", "content": None, "tool_calls": [{"id": "d", "function": {"name": "run_shell_command", "arguments": "{}"}}]}, {"role": "tool", "tool_call_id": "d", "content": "ok"}]
    r = build_reply(_rec("/v1/chat/completions", {"model": "m", "messages": again, "tools": TOOLS_CHAT + [done]}), "tool-call", "")
    assert json.loads(r["body"])["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "submit_and_exit"


def test_messages_reply_sse():
    rec = _rec("/v1/messages", {"model": "m", "stream": True, "messages": [{"role": "user", "content": "hi"}], "tools": TOOLS_MESSAGES})
    r = build_reply(rec, "tool-call", "")
    assert r["content_type"] == "text/event-stream"
    events = _sse_events(r["body"])
    assert [e["type"] for e in events] == ["message_start", "content_block_start", "content_block_delta", "content_block_stop", "message_delta", "message_stop"]
    assert events[1]["content_block"]["name"] == "Bash" and json.loads(events[2]["delta"]["partial_json"]) == {"command": "echo ok"}


def test_chat_reply_json_and_sse():
    rec = _rec("/v1/chat/completions", {"model": "m", "messages": [{"role": "user", "content": "hi"}], "tools": TOOLS_CHAT})
    msg = json.loads(build_reply(rec, "tool-call", "")["body"])
    tc = msg["choices"][0]["message"]["tool_calls"][0]
    assert msg["choices"][0]["finish_reason"] == "tool_calls" and tc["function"]["name"] == "run_shell_command"
    assert json.loads(tc["function"]["arguments"]) == {"command": "echo ok"}
    rec["body"].update(stream=True, stream_options={"include_usage": True})
    raw = build_reply(rec, "tool-call", "")["body"]
    chunks = _sse_events(raw)
    assert raw.endswith(b"data: [DONE]\n\n") and chunks[-1]["usage"]["total_tokens"] == 20
    assert chunks[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "run_shell_command"
    assert chunks[2]["choices"][0]["finish_reason"] == "tool_calls"


def test_chat_reply_text_when_no_tools():
    rec = _rec("/v1/chat/completions", {"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    r = build_reply(rec, "tool-call", "")
    assert r["kind"] == "text" and json.loads(r["body"])["choices"][0]["message"]["content"] == "ok"


def test_responses_reply_function_and_custom_tools():
    fn = _rec("/v1/responses", {"model": "m", "input": [{"role": "user", "content": "hi"}],
                                "tools": [{"type": "function", "name": "bash", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}]})
    out = json.loads(build_reply(fn, "tool-call", "")["body"])["output"][0]
    assert out["type"] == "function_call" and out["name"] == "bash" and json.loads(out["arguments"]) == {"command": "echo ok"}
    custom = _rec("/v1/responses", {"model": "m", "stream": True, "input": [
        {"type": "additional_tools", "role": "developer", "tools": [{"type": "namespace", "name": "functions", "tools": [{"type": "custom", "name": "exec", "format": {"type": "grammar"}}]}]},
        {"role": "user", "content": "hi"}]})
    events = _sse_events(build_reply(custom, "tool-call", "")["body"])
    types = [e["type"] for e in events]
    assert types[0] == "response.created" and types[-1] == "response.completed"
    assert events[-1]["response"]["output"][0] == {"type": "custom_tool_call", "id": events[-1]["response"]["output"][0]["id"],
                                                   "call_id": events[-1]["response"]["output"][0]["call_id"], "name": "exec", "input": "echo ok", "status": "completed"}
    assert all("sequence_number" in e for e in events)


def test_responses_text_reply_sse():
    rec = _rec("/v1/responses", {"model": "m", "stream": True, "input": [{"role": "user", "content": "hi"}]})
    events = _sse_events(build_reply(rec, "tool-call", "")["body"])
    assert "response.output_text.delta" in [e["type"] for e in events]
    assert events[-1]["response"]["output"][0]["content"][0]["text"] == "ok"


def test_gemini_reply_function_call_and_json_mode():
    rec = _rec("/v1beta/models/gemini-x:streamGenerateContent?alt=sse", {"contents": [{"role": "user", "parts": [{"text": "hi"}]}],
               "tools": [{"functionDeclarations": [{"name": "read_file", "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}}]}]})
    r = build_reply(rec, "tool-call", "/w")
    assert r["content_type"] == "text/event-stream"
    data = _sse_events(r["body"])[0]
    assert data["modelVersion"] == "gemini-x"
    assert data["candidates"][0]["content"]["parts"][0] == {"functionCall": {"name": "read_file", "args": {"file_path": "/w/README.md"}}}
    router = _rec("/v1beta/models/gemini-y:generateContent", {"contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                  "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": {"type": "OBJECT", "required": ["complexity_score"], "properties": {"complexity_score": {"type": "INTEGER"}}}}})
    r = build_reply(router, "tool-call", "")
    assert r["content_type"] == "application/json"
    text = json.loads(r["body"])["candidates"][0]["content"]["parts"][0]["text"]
    assert json.loads(text) == {"complexity_score": 1}


def test_unique_tool_call_ids():
    rec = _rec("/v1/chat/completions", {"model": "m", "messages": [{"role": "user", "content": "hi"}], "tools": TOOLS_CHAT})
    ids = {json.loads(build_reply(rec, "tool-call", "")["body"])["choices"][0]["message"]["tool_calls"][0]["id"] for _ in range(3)}
    assert len(ids) == 3


def test_models_listing_and_unknown_paths():
    listing = build_reply(_rec("/v1/models", "", "GET"), "tool-call")
    assert listing["status"] == 200 and "gpt-4o" in [m["id"] for m in json.loads(listing["body"])["data"]]
    info = json.loads(build_reply(_rec("/v1/model/info", "", "GET"), "tool-call")["body"])
    assert any(m["model_name"] == "gpt-5.4" and m["model_info"]["supports_function_calling"] for m in info["data"])
    assert build_reply(_rec("/v1/embeddings", {"input": "x"}), "tool-call")["status"] == 404


# ------------------------------------------------------------------ the recorder end to end

def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_recorder_replies_and_records():
    out = tempfile.mkdtemp()
    port = _free_port()
    t = threading.Thread(target=recorder.serve, args=(out, "127.0.0.1", port, "tool-call", "/w", 2), daemon=True)
    t.start()
    for _ in range(50):
        if socket.socket().connect_ex(("127.0.0.1", port)) == 0:
            break
    body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}], "tools": TOOLS_CHAT}).encode()

    def post():
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=body, headers={"content-type": "application/json", "x-test": "1"})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    s1, b1 = post()
    s2, _ = post()
    s3, b3 = post()
    assert s1 == 200 and json.loads(b1)["choices"][0]["finish_reason"] == "tool_calls"
    assert s2 == 200
    assert s3 == 400 and b"dead end" in b3      # max_requests exhausted
    files = sorted(os.listdir(out))
    assert files == ["req_001.json", "req_002.json", "req_003.json"]
    with open(os.path.join(out, "req_001.json"), encoding="utf-8") as fh:
        rec = json.load(fh)
    assert {k.lower(): v for k, v in rec["headers"].items()}["x-test"] == "1" and rec["body"]["model"] == "m"
    assert rec["reply"] == {"mode": "tool-call", "kind": "tool-call", "status": 200}
