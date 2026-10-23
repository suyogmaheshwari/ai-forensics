# Agent catalog and roadmap

Which agents the toolkit should identify, how each one is captured, and where the work stands.
Status: `rule` = rule and fixture committed, eval passing · `fixture` = capture committed, rule pending ·
`todo` = not captured yet.

## CLI agents (capturable headless, in a container)

Point the agent's base URL at the recorder, run one non-interactive prompt, sanitise, commit.

| Agent | Install | Base-URL setting | Expected protocol | Status |
| --- | --- | --- | --- | --- |
| Claude Code | `npm i -g @anthropic-ai/claude-code` | `ANTHROPIC_BASE_URL` | `/v1/messages` | rule (2.1.299) |
| Codex CLI | `npm i -g @openai/codex` | `-c model_providers.<p>.base_url=…` or `OPENAI_BASE_URL` | `/v1/responses` | rule (0.163.0) |
| Qwen Code | `npm i -g @qwen-code/qwen-code` | `OPENAI_BASE_URL` | `/v1/chat/completions` | todo |
| Gemini CLI | `npm i -g @google/gemini-cli` | `GOOGLE_GEMINI_BASE_URL` | `:generateContent` | todo |
| Aider | `pip install aider-chat` | `OPENAI_API_BASE` | `/v1/chat/completions` | todo |
| OpenCode | `npm i -g opencode-ai` | provider `baseURL` in `opencode.json` | `/v1/chat/completions` | todo |
| Goose | `pip install goose-ai` / binary | `OPENAI_HOST` | `/v1/chat/completions` | todo |
| Cline CLI | `npm i -g cline` | provider settings | `/v1/chat/completions` | todo |
| Kilo Code CLI | `npm i -g @kilocode/cli` | provider settings | `/v1/chat/completions` | todo |
| OpenClaw | see project docs | provider settings | `/v1/chat/completions` | todo |

## Desktop IDEs (capture on a workstation)

Set the custom model's base URL in the IDE settings to the recorder and send one message. These
cannot run in a headless container.

| Agent | Where the base URL is set | Expected protocol | Status |
| --- | --- | --- | --- |
| Trae | Custom model settings | `/v1/chat/completions` | todo |
| Qoder | Custom model settings | `/v1/chat/completions` | todo |
| Zed | `assistant.openai.api_url` in settings.json | `/v1/chat/completions` | todo |
| Windsurf | Custom provider settings | `/v1/chat/completions` | todo |
| Cursor | Override OpenAI base URL (Pro; requests come from Cursor's cloud, recorder must be internet-reachable) | `/v1/chat/completions` | todo |

## Out of scope

Hosted agent platforms (Dify, HiAgent, Copilot) call models from their own servers; the request
identifies the platform, not an installation. They can be allow-listed by credential at a gateway,
which is a policy decision rather than a fingerprint.

## Definition of done for one agent

1. A sanitised fixture under `fixtures/<agent>/` for a named version, with no credentials, ids,
   paths, emails or conversation content.
2. A rule under `rules/` using at least two feature groups, with a deny feature where another
   product shares a layer.
3. `aiforensics eval` at precision 1.0 and recall 1.0 over all fixtures, and the tests green.
4. A line in this table moved to `rule (<version>)`.
