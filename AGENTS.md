# Agent catalog and roadmap

Which agents the toolkit should identify, how each one is captured, and where the work stands.
Status: `rule (<version>)` = fixtures for the session shapes and a rule committed, eval passing ·
`fixture` = capture committed, rule pending · `todo` = not captured yet · `blocked` = cannot be
pointed at the recorder (see the reason).

## CLI agents (capturable headless, in a container)

`capture/run_cli_captures.sh [agent …]` installs each agent under `capture/.work/`, starts the
recorder in reply mode (`--reply tool-call`), runs a first prompt that needs a shell command, then
a follow-up in the same session, and sanitises every model request into
`fixtures/<agent>/<nnn>-cli-<turn>.json`. The table gives the setting that moves the agent's base
URL and the one-shot command; the script holds the exact flags, config files and env isolation. The recorder
also answers `GET /v1/models` and LiteLLM's `GET /v1/model/info` with a fixed model list for the agents that
validate the configured model first (Forge, Junie).

| Agent | Install | Base-URL setting | Headless run | Protocol | Status |
| --- | --- | --- | --- | --- | --- |
| Claude Code | `npm i -g @anthropic-ai/claude-code` | `ANTHROPIC_BASE_URL` | `claude -p "…" --allowedTools Bash`; follow-up `--continue` | `/v1/messages` | rule (2.1.309 CLI; 2.1.306 VS Code) |
| Codex CLI | `npm i -g @openai/codex` | custom provider in `$CODEX_HOME/config.toml`: `[model_providers.x] base_url, env_key, wire_api = "responses"` (the built-in `openai` provider ignores `OPENAI_BASE_URL` since 0.16x) | `codex exec --skip-git-repo-check "…"`; follow-up `codex exec resume --last "…"` | `/v1/responses` | rule (0.164.0; 0.163.0) |
| Qwen Code | `npm i -g @qwen-code/qwen-code` | `OPENAI_BASE_URL` + `OPENAI_API_KEY` + `OPENAI_MODEL` (auth type `openai` in `~/.qwen/settings.json`) | `qwen --approval-mode yolo "…"`; follow-up `--continue` | `/v1/chat/completions` | rule (0.26.0) |
| Gemini CLI | `npm i -g @google/gemini-cli` | `GOOGLE_GEMINI_BASE_URL` + `GEMINI_API_KEY` (auth type `gemini-api-key` in `settings.json`) | `gemini --skip-trust --approval-mode yolo -p "…"`; follow-up `--resume latest` | `:streamGenerateContent` (+ a `:generateContent` model-router side request) | rule (0.65.0) |
| Aider | `pip install aider-chat` (Python 3.10–3.12) | `OPENAI_API_BASE` + `OPENAI_API_KEY`, `--model openai/<m>` | `aider --message "…" --yes-always --no-git`; follow-up `--restore-chat-history` | `/v1/chat/completions` (no tools; edit blocks in text) | rule (0.86.2) |
| OpenCode | `npm i -g opencode-ai` | `provider.openai.options.baseURL` in `opencode.json` | `opencode run --model openai/<m> "…"`; follow-up `--continue` | `/v1/responses` (+ a title-generator side request) | rule (1.19.4) |
| Goose | `curl -fsSL https://github.com/block/goose/releases/download/stable/download_cli.sh \| bash` | `OPENAI_HOST` (`GOOSE_PROVIDER=openai`) or `ANTHROPIC_HOST` (`GOOSE_PROVIDER=anthropic`) | `goose run -t "…" --name x`; follow-up `--resume` | `/v1/chat/completions` or `/v1/messages` (+ a title side request and `GET /v1/models`) | rule (1.56.0, both providers) |
| Cline CLI | `npm i -g cline` | `cline auth openai -k <key> -b <url> -m <model>` (stored in `<data-dir>/settings/providers.json`) | `cline --yolo --json -P openai -m <m> "…"`; follow-up: see the script | `/v1/chat/completions` | rule (3.0.73) |
| Kilo Code CLI | `npm i -g @kilocode/cli` | `provider.openai.options.baseURL` in `kilo.json` (OpenCode fork) | `kilocode run --model openai/<m> "…"`; follow-up `--continue` | `/v1/responses` | rule (7.8.17) |
| OpenClaw | `npm i -g openclaw` | `models.providers.<p>.baseUrl` in `~/.openclaw/openclaw.json` (`api: openai-completions`); attribution headers (`originator`, `version`, own user-agent) are only sent when the `openai` provider's base URL names `api.openai.com`, so the script also captures that variant by keeping the hostname and preloading a `net.connect` shim that sends the connection to the recorder | `openclaw agent --local --session-id x -m "…"`; follow-up: same `--session-id` | `/v1/chat/completions` | rule (2026.6.35; custom provider, openai provider with proxy URL, and native api.openai.com route) |
| Pi coding agent | `npm i -g @mariozechner/pi-coding-agent` | `providers.<n>.baseUrl` + `api` in `~/.pi/agent/models.json` | `pi -p "…"` | selectable: chat completions, responses, messages, Gemini | todo |
| Crush | `npm i -g @charmland/crush` | `crushrc`: `provider add x --type openai-compat --base-url …` (anthropic type too) | `crush run -q "…"`; follow-up `--continue` | `/v1/chat/completions` (+ a title side request) | rule (0.98.2) |
| Grok CLI | `npm i -g @vibe-kit/grok-cli` | `GROK_BASE_URL` (+ any `GROK_API_KEY`) | `grok -p "…"` (one-shot, no headless resume) | `/v1/chat/completions` (xAI `search_parameters` key) | rule (0.0.34) |
| Kimi Code CLI | `npm i -g @moonshot-ai/kimi-code` | `[providers.x] type, base_url, api_key` in `~/.kimi-code/config.toml` | `kimi -p "…"`; follow-up `-c -p` | `/v1/chat/completions` (anthropic, responses, Gemini types configurable) | rule (2.3.1) |
| Mistral Vibe | `pip install mistral-vibe` | `[[providers]] api_base, api_style` in `~/.vibe/config.toml` | `vibe -p "…" --yolo --trust`; follow-up `-c` | `/v1/chat/completions` (`openai-responses`, `anthropic` styles configurable) | rule (3.0.2) |
| GitHub Copilot CLI (BYOK) | `npm i -g @github/copilot` | `COPILOT_PROVIDER_BASE_URL`, `COPILOT_PROVIDER_TYPE` (`openai`/`anthropic`), `COPILOT_PROVIDER_API_KEY`, `COPILOT_PROVIDER_WIRE_API=responses` | `copilot -p "…" --allow-all`; follow-up `--continue`; **no GitHub login with BYOK** (`copilot help environment`) | `/v1/chat/completions`, `/v1/messages`, `/v1/responses` | rule (1.0.103, all three) |
| Continue CLI | `npm i -g @continuedev/cli` | `models[].apiBase` in a local `config.yaml` | `cn -p "…"` | chat completions or messages | todo |
| OpenHands CLI | `uv tool install openhands --python 3.12` / `pip install openhands` | `LLM_BASE_URL`, `LLM_MODEL=openai/<m>`, `LLM_API_KEY` with `--override-with-envs` | `openhands --headless -t "…" --always-approve`; follow-up `--resume <id>` | `/v1/chat/completions` (LiteLLM; non-streaming, prompt_cache_key) | rule (1.16.0) |
| gptme | `pipx install gptme` | `OPENAI_BASE_URL`, model `local/<m>` | `gptme -n "…"` | `/v1/chat/completions` | todo |
| Forge | `npm i -g forgecode` | `~/.forge/.credentials.json` (`OPENAI_URL` or `ANTHROPIC_URL` url_params; the env migration and `provider login` need a tty); model must appear in `GET /v1/models` | `forge -p "…"`; follow-up `--conversation-id` | `/v1/chat/completions` or `/v1/messages` (+ `GET /v1/models` and a title side request) | rule (2.13.21, both) |
| iFlow CLI | `npm i -g @iflow-ai/iflow-cli` | `IFLOW_BASE_URL`, `IFLOW_API_KEY`, `IFLOW_MODEL_NAME` (auth type `openai-compatible`) | `iflow -p "…" --yolo` | `/v1/chat/completions` | skipped (service announced for shutdown); its `iFlow-Cli` user-agent and prompt are deny features on the Qwen Code and Gemini CLI rules |
| CodeBuddy Code | `npm i -g @tencent-ai/codebuddy-code` | `~/.codebuddy/models.json` (`url`, `apiKey`, `vendor`; `api: openai-responses` for Responses) | `codebuddy -p "…" -y`; follow-up `--continue`; **no login needed in -p mode with a custom model** | `/v1/chat/completions` | rule (2.174.0) |
| Open Interpreter | legacy: `pip install open-interpreter` (0.4.3); current: `curl -fsSL https://www.openinterpreter.com/install \| sh` (GitHub releases only) | legacy: `--api_base`; current: `[model_providers.<id>] base_url, wire_api` in `config.toml` | legacy: `echo "…" \| interpreter --stdin -y` (SDK `chat()` for follow-ups); current: `interpreter exec "…"` | legacy `/v1/chat/completions`; current: Codex's wire shape (`originator: codex_exec`, `codex_exec/0.159.0`) with harnesses that imitate Claude Code's headers | rule (legacy 0.4.3); current CLI blocked in this container (installer needs github.com releases) and identifies as Codex/Claude Code by design |
| Hermes Agent | `curl -fsSL https://hermes-agent.nousresearch.com/install.sh \| bash` | `--provider custom --base-url …` | `hermes chat -q "…" --oneshot --yolo` | `/v1/chat/completions` | todo |
| Junie CLI | `npm i -g @jetbrains/junie` (downloads a jpackage app with a bundled JRE into `~/.local/share/junie`) | `--provider=litellm --litellm-url=… --litellm-api-key=…` (`JUNIE_LLM_PROVIDER`, `JUNIE_LITELLM_URL`); model must be a Junie built-in name present in `GET /v1/model/info` | `junie --task "…" --output-format=json`; follow-up `--resume --session-id=…`; **no JetBrains login with the litellm provider** | `/v1/chat/completions` (non-streaming; + task and step summariser side requests) | rule (26.9.7) |
| mini-swe-agent | `pip install mini-swe-agent` | `model.model_kwargs.api_base` in `mini.yaml` | `mini -t "…" -y` | `/v1/chat/completions` (LiteLLM) | todo |
| Trae Agent (open source) | `git clone bytedance/trae-agent && uv sync --extra evaluation` | `model_providers.x.base_url` in `trae_config.yaml` (`--model-base-url`) | `trae-cli run "…" --provider … --model … --config-file …`; follow-up only via `trae-cli interactive` fed on stdin | `/v1/responses` (openai), `/v1/chat/completions` (openrouter), `/v1/messages` (anthropic); never streams | rule (e839e55, all three) |
| nanobot | `pip install nanobot-ai` | `providers.custom.apiBase` + `agents.defaults.provider: custom` in `~/.nanobot/config.json` (after `nanobot onboard`) | `nanobot agent -m "…" -s <session>`; follow-up: same `-s` | `/v1/chat/completions` | rule (0.3.5) |

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
| Void | Provider settings (OpenAI-compatible) | `/v1/chat/completions` | todo |
| Cline, Roo Code, Kilo Code extensions | Provider settings of the VS Code extension | `/v1/chat/completions`, `/v1/messages` | todo (the Cline and Kilo CLIs above share their lineage) |
| Continue extension | `models[].apiBase` in `config.yaml` | `/v1/chat/completions` | todo |

## Blocked: no custom endpoint, login-bound or hosted

These talk only to their vendor's backend, or need a vendor login before any request leaves the
machine, so a gateway sees the platform's traffic rather than an installation's. They can be
allow-listed by credential at a gateway, which is a policy decision rather than a fingerprint.

| Agent | Why |
| --- | --- |
| Amp (`@ampcode/cli`) | `AMP_API_KEY` mandatory; custom model URLs are configured server-side |
| Cursor CLI (`agent`) | `CURSOR_API_KEY` or login only, no base-URL option |
| Kiro CLI (ex Amazon Q CLI) | login only, no BYOK |
| Auggie (`@augmentcode/auggie`) | `auggie login` only |
| Rovo Dev CLI | Atlassian token; only `agent.modelId` is configurable |
| Qodo Command (`@qodo/command`) | sign-in required, no endpoint setting |
| Warp Oz CLI | `WARP_API_KEY`, no BYO model |
| Devin CLI | login; custom endpoints only in air-gapped builds |
| Factory Droid CLI | `FACTORY_API_KEY` mandatory even with BYOK `customModels` |
| Cody CLI | talks only to a Sourcegraph instance |
| Plandex | custom providers need the self-hosted server |
| Zencoder, Tabnine, Sourcery, Blackbox, Gemini Code Assist, Firebender | IDE plugins without a base-URL override |
| Replit Agent, Jules, Codex Web, Dify, HiAgent, Copilot (hosted) | call models from the vendor's servers; no local client |
| Vibe Kanban, Emdash, Conductor | orchestrators that spawn the agents above; not agents themselves |

## Definition of done for one agent

1. Sanitised fixtures under `fixtures/<agent>/` for a named version covering the session shapes:
   first turn, after a tool call, follow-up (and each entry point where they differ), with no
   credentials, ids, paths, emails or conversation content.
2. A rule under `rules/` using at least two feature groups, with a deny feature where another
   product shares a layer.
3. `aiforensics eval` at precision 1.0 and recall 1.0 over all fixtures, which means the rule
   identifies every turn shape of its agent and none of any other agent's, and the tests green.
4. A line in this table moved to `rule (<version>)`.
