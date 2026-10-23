import json

from aiforensics.features import extract
from aiforensics.sanitize import sanitize

RAW_GEMINI = {
    "method": "POST", "path": "/v1beta/models/gemini-x:streamGenerateContent?alt=sse",
    "headers": {"user-agent": "GeminiCLI-tui/1.0.0/gemini-x (linux; x64; terminal)", "x-goog-api-key": "AIza-secret", "host": "127.0.0.1:1",
                "x-goog-api-client": "google-genai-sdk/1.0 gl-node/v22"},
    "body": {
        "systemInstruction": {"role": "user", "parts": [{"text": "You are Gemini CLI, an autonomous agent. " + "x" * 400}]},
        "contents": [{"role": "user", "parts": [{"text": "secret prompt at /home/me/proj with me@example.com"}]},
                     {"role": "model", "parts": [{"functionCall": {"name": "read_file", "args": {"file_path": "/home/me/proj/README.md"}}}]},
                     {"role": "user", "parts": [{"functionResponse": {"name": "read_file", "response": {"output": "x"}}}]}],
        "tools": [{"functionDeclarations": [{"name": "read_file", "description": "Reads a file", "parametersJsonSchema": {"type": "object"}},
                                            {"name": "glob", "description": "Finds files", "parametersJsonSchema": {"type": "object"}}]}],
        "generationConfig": {"temperature": 1, "topP": 0.95, "thinkingConfig": {"includeThoughts": True}, "responseMimeType": "application/json",
                             "responseJsonSchema": {"type": "OBJECT", "properties": {"score": {"type": "INTEGER"}}}},
    },
    "reply": {"mode": "tool-call", "kind": "text", "status": 200},
}

RAW_CHAT = {
    "method": "POST", "path": "/v1/chat/completions",
    "headers": {"authorization": "Bearer sk-secret", "user-agent": "OpenAI/JS 6.0", "traceparent": "00-abc-def-01", "agent-session-id": "20261023_1"},
    "body": {"model": "gpt", "stream": True, "stream_options": {"include_usage": True}, "max_completion_tokens": 8192,
             "messages": [{"role": "system", "content": "You are a personal assistant running inside OpenClaw.\n## Tooling\nsecret /Users/me/x"},
                          {"role": "user", "content": "[2026-10-23 18:25] say hi"},
                          {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "exec", "arguments": "{}"}}]},
                          {"role": "tool", "tool_call_id": "c1", "name": "exec", "content": "ok"}],
             "tools": [{"type": "function", "function": {"name": "exec", "description": "Run shell commands", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}}}]},
}


def test_gemini_capture_is_sanitised_and_still_extractable():
    fx = sanitize(RAW_GEMINI)
    text = json.dumps(fx)
    assert "AIza-secret" not in text and "secret prompt" not in text and "/home/me" not in text and "example.com" not in text
    assert "parametersJsonSchema" not in text and "responseJsonSchema" not in text
    assert fx["turn"] == "after-tool-call" and fx["recorder_reply"] == "text"
    assert fx["body"]["systemInstruction"]["parts"][0]["text_prefix"].startswith("You are Gemini CLI")
    assert fx["body"]["systemInstruction"]["parts"][0]["text_len"] == 441
    assert fx["body"]["tools"][0]["functionDeclarations"][0] == {"name": "read_file", "type": None, "description_prefix": "Reads a file"}
    assert fx["body"]["contents"] == [{"role": "user", "parts": ["text"]}, {"role": "model", "parts": ["functionCall"], "tool_calls": ["read_file"]},
                                      {"role": "user", "parts": ["functionResponse"]}]
    assert fx["body"]["generationConfig"] == {"temperature": 1, "topP": 0.95, "thinkingConfig": {"includeThoughts": True},
                                              "responseMimeType": "application/json", "responseSchema_keys": ["score"]}
    feats = extract(fx)
    assert feats["family"] == "gemini" and feats["model"] == "gemini-x"
    assert feats["tools"] == ["glob", "read_file"]
    assert feats["identity"][0].startswith("You are Gemini CLI")
    assert feats["habits"]["generationConfig"] is True and feats["habits"]["generationConfig.temperature"] == 1
    assert extract(RAW_GEMINI)["tools"] == feats["tools"]


def test_chat_capture_keeps_shape_not_content():
    fx = sanitize(RAW_CHAT)
    text = json.dumps(fx)
    assert "sk-secret" not in text and "say hi" not in text and "/Users/me" not in text
    assert fx["headers"]["traceparent"] == "<redacted-id>" and fx["headers"]["agent-session-id"] == "<redacted-id>"
    assert "authorization" not in fx["headers"]
    assert fx["turn"] == "after-tool-call"
    assert fx["body"]["max_completion_tokens"] == 8192 and fx["body"]["stream_options"] == {"include_usage": True}
    msgs = fx["body"]["messages"]
    assert msgs[0]["content"][0]["text_prefix"].startswith("You are a personal assistant running inside OpenClaw")
    assert msgs[1] == {"role": "user", "content": "<redacted>", "content_types": ["text"]}
    assert msgs[2]["tool_calls"] == ["exec"]
    assert msgs[3] == {"role": "tool", "content": "<redacted>", "content_types": ["text"], "name": "exec"}
    assert fx["body"]["tools"] == [{"name": "exec", "type": "function", "description_prefix": "Run shell commands"}]
    assert extract(fx)["identity"][0].startswith("You are a personal assistant running inside OpenClaw")
    assert extract(fx)["tools"] == extract(RAW_CHAT)["tools"] == ["exec"]


def test_responses_capture_keeps_namespaces_and_calls():
    raw = {"method": "POST", "path": "/v1/responses", "headers": {"originator": "codex_exec"},
           "body": {"model": "m", "store": False, "input": [
               {"type": "additional_tools", "role": "developer", "tools": [{"type": "namespace", "name": "functions", "tools": [{"type": "custom", "name": "exec", "description": "Runs shell"}]}]},
               {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "You are Codex, an agent."}]},
               {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "private"}]},
               {"type": "custom_tool_call", "call_id": "c", "name": "exec", "input": "echo ok"},
               {"type": "custom_tool_call_output", "call_id": "c", "output": "ok"}]}}
    fx = sanitize(raw)
    assert "private" not in json.dumps(fx) and "echo ok" not in json.dumps(fx)
    assert fx["body"]["input"][0]["tools"] == [{"name": "functions", "type": "namespace", "tools": [{"name": "exec", "type": "custom", "description_prefix": "Runs shell"}]}]
    assert fx["body"]["input"][3] == {"type": "custom_tool_call", "role": None, "name": "exec"}
    assert fx["turn"] == "after-tool-call"
    assert extract(fx)["tools"] == extract(raw)["tools"] == ["exec"]
    assert extract(fx)["identity"] == ["You are Codex, an agent."]
