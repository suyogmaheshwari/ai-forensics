# ai-forensics

Identify **which AI coding agent sent an LLM API request**, from the request alone.

Coding agents such as Claude Code, Codex CLI, Qwen Code, Aider or OpenCode all call the same
model APIs, but each one leaves a distinct fingerprint: the protocol it speaks, the headers its
client adds, the system prompt it hard-codes and the tools it registers. This toolkit captures
those requests, keeps a sanitised fixture per agent and version, and scores new requests against
a small rule catalog. It is meant for gateway and security teams who need to know *what* is
talking to their models, without trusting a User-Agent string alone.

## The three-layer model

| Layer | What it is | Example (Codex CLI 0.163) |
| --- | --- | --- |
| 1. Protocol | API family from the path | `POST /v1/responses` |
| 2. Headers | Product and SDK headers | `originator: codex_exec`, `user-agent: codex_exec/0.163.0 (…)` |
| 3. Body | Identity prompt prefix, tool-name set, parameter habits | "You are Codex, an agent based on…", tools `shell`/`apply_patch`, `store: false` |

Layer 1 separates protocol families. Layer 2 is cheap and usually unique, but any client can drop
or spoof a header, so it is evidence, not proof. Layer 3 is what tells apart the many agents that
share one protocol. A rule combines features from several layers with weights, per-layer caps
and a threshold, so no single spoofable field can carry a verdict and a missing field does not
break identification.

## Quick start

```bash
pip install -e .

# 1. run the dead-end recorder (answers 400; the agent still sends its full request)
aiforensics record --out capture/raw/my_agent

# 2. point an agent at it and send one prompt
ANTHROPIC_BASE_URL=http://127.0.0.1:18599 ANTHROPIC_API_KEY=dummy claude -p "say hi"
OPENAI_BASE_URL=http://127.0.0.1:18599/v1 OPENAI_API_KEY=dummy codex exec "say hi"

# 3. identify a captured request
aiforensics identify capture/raw/my_agent/req_001.json

# 4. make a shareable fixture (credentials, ids, paths, emails and conversation content removed)
aiforensics sanitize capture/raw/my_agent/req_001.json --agent my_agent --version 1.2.3 \
    -o fixtures/my_agent/001.json

# 5. measure the rules over all fixtures
aiforensics eval
```

No vendor account or network is needed for capture: the agent builds its request before it ever
sees a reply, so a dead end on localhost is enough. Desktop IDEs (Trae, Qoder, Cursor, …) are
captured the same way by setting their custom-model base URL to the recorder.

## Rules

One JSON file per product under `rules/`; the format and operators are documented in
`aiforensics/rules.py`. Scoring is additive with per-group caps (`header`, `identity`, `tools`,
`habits`), a minimum number of distinct groups, deny features that veto a product, and a
threshold. Exactly one eligible product is `identified`; several is `conflict`; none is `unknown`.

Current catalog: Claude Code, Codex CLI. The target list, capture method per agent and the
definition of done are in [AGENTS.md](AGENTS.md). Contributions of captures are welcome: run
steps 1, 2 and 4 above and open a pull request with the fixture.

## Layout

```
aiforensics/   recorder, sanitiser, feature extractor, rule engine, scorer, CLI
rules/         one rule per product
fixtures/      sanitised captures, one directory per product (the eval labels)
capture/raw/   your raw captures (git-ignored: they contain prompts and identifiers)
tests/         unit tests
```

## Privacy

Raw captures contain the full system prompt, your conversation, working directory, email and
session identifiers. Only sanitised fixtures are committed: they keep the first 200 characters of
each identity text, tool names and descriptions, headers without credentials or ids, and the
parameter habits, nothing else.

MIT licensed.
