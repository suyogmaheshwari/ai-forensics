"""HTTP recorder.

Point any agent's base URL at it (ANTHROPIC_BASE_URL, OPENAI_BASE_URL, or the
IDE's custom-model setting). Every request is written to a JSON file. In the
default dead-end mode every request is answered with 400, so no vendor account
or network is needed: the agent builds its full request before it ever sees a
reply. With `--reply tool-call` the recorder answers with one harmless tool call
and then a final text, so the agent also sends its after-tool-call turn.
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import time

from .reply import build_reply

DEAD_END = b'{"type":"error","error":{"type":"invalid_request_error","message":"aiforensics recorder: dead end"}}'


def serve(out_dir: str, host: str = "127.0.0.1", port: int = 18599, reply: str = "dead-end",
          work_dir: str = "", max_requests: int = 0) -> None:
    os.makedirs(out_dir, exist_ok=True)
    counter = {"n": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _record(self) -> None:
            counter["n"] += 1
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw)
            except Exception:
                body = raw.decode("utf-8", "replace")
            record = {
                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers),
                "body": body,
            }
            exhausted = max_requests and counter["n"] > max_requests
            if reply == "dead-end" or exhausted:
                answer = {"status": 400, "content_type": "application/json", "body": DEAD_END, "kind": "dead-end"}
            else:
                answer = build_reply(record, reply, work_dir)
            record["reply"] = {"mode": reply, "kind": answer["kind"], "status": answer["status"]}
            path = os.path.join(out_dir, f"req_{counter['n']:03d}.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(record, fh, ensure_ascii=False, indent=1)
            sys.stderr.write(f"recorded {self.command} {self.path} -> {path} ({length} bytes) reply={answer['kind']}\n")
            self.send_response(answer["status"])
            self.send_header("content-type", answer["content_type"])
            self.send_header("content-length", str(len(answer["body"])))
            self.end_headers()
            self.wfile.write(answer["body"])

        do_POST = _record
        do_GET = _record

        def log_message(self, *args):  # silence default access log
            pass

    sys.stderr.write(f"aiforensics recorder listening on http://{host}:{port}, writing to {out_dir}, reply={reply}\n")
    http.server.ThreadingHTTPServer((host, port), Handler).serve_forever()
