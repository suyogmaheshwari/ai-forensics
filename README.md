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

# 1. run the recorder. Dead-end mode answers 400; reply mode answers the first turn with one
#    harmless tool call (echo ok) and the next with a text, so the agent also sends its
#    after-tool-call turn and the session ends cleanly.
aiforensics record --out capture/raw/my_agent --reply tool-call --work-dir "$PWD"

# 2. point an agent at it and send one prompt that needs a shell command
ANTHROPIC_BASE_URL=http://127.0.0.1:18599 ANTHROPIC_API_KEY=dummy claude -p "run echo ok" --allowedTools Bash
OPENAI_BASE_URL=http://127.0.0.1:18599/v1 OPENAI_API_KEY=dummy qwen --approval-mode yolo "run echo ok"

# 3. identify a captured request
aiforensics identify capture/raw/my_agent/req_001.json

# 4. make shareable fixtures (credentials, ids, paths, emails and conversation content removed),
#    one per turn shape: first-turn, after-tool-call, followup
aiforensics shape capture/raw/my_agent/req_*.json
aiforensics sanitize capture/raw/my_agent/req_001.json --agent my_agent --version 1.2.3 \
    -o fixtures/my_agent/001-cli-first-turn.json

# 5. measure the rules over all fixtures
aiforensics eval
```

No vendor account or network is needed for capture: the agent builds its request before it ever
sees a reply, so a dummy key and localhost are enough. `capture/run_cli_captures.sh` does steps
1–4 for every headless CLI agent in the catalog (install, isolated home, first prompt, follow-up
in the same session, sanitise); `AIFORENSICS_SANITIZE_ONLY=1` rebuilds the fixtures from
`capture/raw/` after a sanitiser change. Desktop IDEs (Trae, Qoder, Cursor, …) are captured the
same way by setting their custom-model base URL to the recorder. Codex CLI needs a custom model
provider in `config.toml` (its built-in provider ignores `OPENAI_BASE_URL`); see `AGENTS.md`.

## Rules

One JSON file per product under `rules/`; the format and operators are documented in
`aiforensics/rules.py`. Scoring is additive with per-group caps (`header`, `identity`, `tools`,
`habits`), a minimum number of distinct groups, deny features that veto a product, and a
threshold. Exactly one eligible product is `identified`; several is `conflict`; none is `unknown`.

A rule must identify every turn of its agent's session: the first request, the request after a
tool call and a follow-up message, plus side requests such as title generators and model routers.
Each rule's `notes` field records which features were seen to vary between turns or versions.

Current catalog: Claude Code, Codex CLI, Qwen Code, Gemini CLI, Aider, OpenCode, Goose, Cline
CLI, Kilo Code CLI, OpenClaw, Crush, Kimi Code CLI, CodeBuddy Code, GitHub Copilot CLI (BYOK),
OpenHands CLI, Forge, nanobot, Trae Agent, Grok CLI, Mistral Vibe, Junie CLI, mini-swe-agent, gptme
and the legacy Open Interpreter. The target list, capture method per agent and the definition of done are in
[AGENTS.md](AGENTS.md). Contributions of captures are welcome: run steps 1, 2 and 4
above (or the capture script) and open a pull request with the fixtures.

## Layout

```
aiforensics/   recorder (dead end or reply mode), sanitiser, feature extractor, rule engine, scorer, CLI
rules/         one rule per product
fixtures/      sanitised captures, one directory per product (the eval labels), one file per turn shape
capture/       run_cli_captures.sh; raw/ and .work/ are git-ignored (prompts, identifiers, installs)
tests/         unit tests
```

## Privacy

Raw captures contain the full system prompt, your conversation, working directory, email and
session identifiers. Only sanitised fixtures are committed: they keep the first 200 characters of
each identity text, tool names and descriptions, headers without credentials or ids, the
parameter habits and the shape of the conversation (roles, block types, names of tools called),
nothing else.

MIT licensed.
