#!/usr/bin/env bash
# Install headless CLI coding agents, point each one at the recorder on 127.0.0.1:18599 with a
# dummy key, capture one session per agent (first turn, the turn after a tool call, a follow-up
# message) and sanitise every model request into fixtures/<agent>/<nnn>-cli-<turn>.json.
#
#   capture/run_cli_captures.sh                 # all agents
#   capture/run_cli_captures.sh gemini_cli aider
#
# Raw captures land in capture/raw/<agent>/ (git-ignored). Installs go to capture/.work/ so the
# machine's own tools are left alone. The recorder runs in reply mode: it answers the first turn
# with one harmless tool call (echo ok, or a read of README.md) and the next turn with a text.
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$ROOT/capture/.work
RAW=$ROOT/capture/raw
PORT=18599
BASE=http://127.0.0.1:$PORT
HOMES=${AIFORENSICS_HOMES:-/tmp/aiforensics-homes}   # agent homes must not sit inside this repo: agents read CLAUDE.md and git status
NPM_PREFIX=$WORK/npm
BIN=$NPM_PREFIX/bin
PROMPT1='Run the shell command `echo ok` and tell me its output.'
PROMPT2='Thanks. Now say bye.'
ALL_AGENTS="claude_code codex qwen_code gemini_cli aider opencode goose cline kilocode openclaw crush kimi_code codebuddy copilot openhands forge nanobot trae_agent grok_cli mistral_vibe junie open_interpreter mini_swe_agent gptme continue pi zcode shell_gpt vecli hermes pool"
AGENTS=${*:-$ALL_AGENTS}
PY=${PYTHON:-python3}
CLEAN_PATH=$BIN:$(dirname "$(command -v node || echo /usr/bin/node)"):/usr/local/bin:/usr/bin:/bin

mkdir -p "$WORK" "$RAW" "$NPM_PREFIX"
cd "$ROOT"

log() { printf '\n== %s\n' "$*" >&2; }

npm_install() {  # npm_install <package> <binary>
  [ -x "$BIN/$2" ] && return 0
  timeout 600 npm install -g --prefix "$NPM_PREFIX" "$1" >/dev/null 2>&1 || { log "npm install $1 failed"; return 1; }
}

fresh_home() {  # fresh_home <agent> -> prints an isolated HOME (outside this git tree) with an empty project dir inside
  local h=$HOMES/$1
  rm -rf "$h"; mkdir -p "$h/project"
  printf '# probe\n\nA scratch project for ai-forensics captures.\n' > "$h/project/README.md"
  echo "$h"
}

RECORDER_PID=
start_recorder() {  # start_recorder <agent> <work-dir>
  stop_recorder
  rm -rf "$RAW/$1"; mkdir -p "$RAW/$1"
  "$PY" -m aiforensics.cli record --out "$RAW/$1" --port $PORT --reply tool-call --work-dir "$2" --max-requests 32 \
    >>"$WORK/recorder.log" 2>&1 &
  RECORDER_PID=$!
  for _ in $(seq 1 50); do
    "$PY" - <<EOF && return 0
import socket,sys
s=socket.socket(); s.settimeout(0.2)
sys.exit(0 if s.connect_ex(("127.0.0.1",$PORT))==0 else 1)
EOF
    sleep 0.2
  done
  log "recorder did not start"; return 1
}
stop_recorder() { [ -n "$RECORDER_PID" ] && kill "$RECORDER_PID" 2>/dev/null; RECORDER_PID=; sleep 0.3; }
trap stop_recorder EXIT

# run <home> <project> <env...> -- <command...> : run the agent with a clean environment, no stdin, 3 min cap
run() {
  local home=$1 project=$2; shift 2
  local envs=()
  while [ "$1" != "--" ]; do envs+=("$1"); shift; done; shift
  (cd "$project" && timeout 180 env -i PATH="$CLEAN_PATH" HOME="$home" TERM=dumb NO_COLOR=1 "${envs[@]}" "$@" <"${RUN_STDIN:-/dev/null}") \
    >"$WORK/last_run.log" 2>&1
  log "exit $? : $(tail -c 300 "$WORK/last_run.log" | tr '\n' ' ')"
}

sanitize_all() {  # sanitize_all <agent> <version> [entrypoint] [raw-dir]; numbering continues after existing fixtures
  local agent=$1 version=$2 entry=${3:-cli} raw=${4:-$RAW/$1} line n
  mkdir -p "$ROOT/fixtures/$agent"
  printf '%s\n' "$version" > "$raw/VERSION"
  n=$(ls "$ROOT/fixtures/$agent" | grep -c '\.json$')
  for f in "$raw"/req_*.json; do
    [ -e "$f" ] || continue
    line=$("$PY" -m aiforensics.cli shape "$f")
    local family turn; family=$(cut -f2 <<<"$line"); turn=$(cut -f3 <<<"$line")
    [ "$family" = other ] && continue   # GET /v1/models and other non-model requests
    "$PY" -m aiforensics.cli sanitize "$f" --agent "$agent" --version "$version" -o "$WORK/fixture.tmp.json" >/dev/null
    # agents that loop (retries, nags) produce identical requests; keep one fixture per distinct shape
    "$PY" - "$WORK/fixture.tmp.json" "$ROOT/fixtures/$agent" <<'EOF' || continue
import json, os, sys
new = json.load(open(sys.argv[1], encoding="utf-8"))
for name in os.listdir(sys.argv[2]):
    if name.endswith(".json") and json.load(open(os.path.join(sys.argv[2], name), encoding="utf-8")) == new:
        sys.exit(1)
EOF
    n=$((n+1))
    mv "$WORK/fixture.tmp.json" "$ROOT/fixtures/$agent/$(printf '%03d' "$n")-$entry-$turn.json"
  done
  log "$agent $version: $(ls "$ROOT/fixtures/$agent" | grep -c '\.json$') fixtures"
}

cap_claude_code() {
  npm_install @anthropic-ai/claude-code claude || return
  local v; v=$("$BIN/claude" --version | cut -d' ' -f1)
  local h; h=$(fresh_home claude_code); start_recorder claude_code "$h/project"
  local env=(ANTHROPIC_BASE_URL=$BASE ANTHROPIC_API_KEY=dummy CLAUDE_CONFIG_DIR=$h/.claude DISABLE_AUTOUPDATER=1
             CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 DISABLE_TELEMETRY=1)
  run "$h" "$h/project" "${env[@]}" -- "$BIN/claude" -p "$PROMPT1" --allowedTools Bash
  run "$h" "$h/project" "${env[@]}" -- "$BIN/claude" -p "$PROMPT2" --allowedTools Bash --continue
  stop_recorder; sanitize_all claude_code "$v"
}

cap_codex() {
  npm_install @openai/codex codex || return
  local v; v=$("$BIN/codex" --version | awk '{print $NF}')
  local h; h=$(fresh_home codex); start_recorder codex "$h/project"
  mkdir -p "$h/.codex"
  cat > "$h/.codex/config.toml" <<EOF
model_provider = "recorder"
[model_providers.recorder]
name = "recorder"
base_url = "$BASE/v1"
env_key = "OPENAI_API_KEY"
wire_api = "responses"
EOF
  local env=(CODEX_HOME=$h/.codex OPENAI_API_KEY=dummy)
  run "$h" "$h/project" "${env[@]}" -- "$BIN/codex" exec --skip-git-repo-check -s workspace-write "$PROMPT1"
  run "$h" "$h/project" "${env[@]}" -- "$BIN/codex" exec resume --last --skip-git-repo-check "$PROMPT2"
  stop_recorder; sanitize_all codex "$v"
}

cap_qwen_code() {
  npm_install @qwen-code/qwen-code qwen || return
  local v; v=$("$BIN/qwen" --version | tail -1)
  local h; h=$(fresh_home qwen_code); start_recorder qwen_code "$h/project"
  mkdir -p "$h/.qwen"
  cat > "$h/.qwen/settings.json" <<'EOF'
{"security": {"auth": {"selectedType": "openai"}}, "general": {"disableAutoUpdate": true, "disableUpdateNag": true},
 "privacy": {"usageStatisticsEnabled": false}, "telemetry": {"enabled": false}}
EOF
  local env=(OPENAI_API_KEY=dummy OPENAI_BASE_URL=$BASE/v1 OPENAI_MODEL=qwen3-coder-plus QWEN_CODE_SUPPRESS_YOLO_WARNING=1)
  run "$h" "$h/project" "${env[@]}" -- "$BIN/qwen" --approval-mode yolo --sandbox false "$PROMPT1"
  run "$h" "$h/project" "${env[@]}" -- "$BIN/qwen" --approval-mode yolo --sandbox false --continue "$PROMPT2"
  stop_recorder; sanitize_all qwen_code "$v"
}

cap_gemini_cli() {
  npm_install @google/gemini-cli gemini || return
  local v; v=$("$BIN/gemini" --version | tail -1)
  local h; h=$(fresh_home gemini_cli); start_recorder gemini_cli "$h/project"
  mkdir -p "$h/.gemini"
  cat > "$h/.gemini/settings.json" <<'EOF'
{"security": {"auth": {"selectedType": "gemini-api-key"}}, "privacy": {"usageStatisticsEnabled": false},
 "general": {"disableAutoUpdate": true, "disableUpdateNag": true}, "telemetry": {"enabled": false}}
EOF
  local env=(GEMINI_CLI_HOME=$h GEMINI_API_KEY=dummy GOOGLE_GEMINI_BASE_URL=$BASE GEMINI_CLI_NO_RELAUNCH=1 NO_BROWSER=true
             GEMINI_TELEMETRY_ENABLED=false)
  run "$h" "$h/project" "${env[@]}" -- "$BIN/gemini" --sandbox false --skip-trust --approval-mode yolo -p "$PROMPT1"
  run "$h" "$h/project" "${env[@]}" -- "$BIN/gemini" --sandbox false --skip-trust --approval-mode yolo --resume latest -p "$PROMPT2"
  stop_recorder; sanitize_all gemini_cli "$v"
}

cap_aider() {
  local venv=$WORK/aider-venv
  if [ ! -x "$venv/bin/aider" ]; then
    local py; py=$(command -v python3.12 || command -v python3.11 || command -v python3.10 || echo "$PY")
    "$py" -m venv "$venv" && timeout 900 "$venv/bin/pip" install -q aider-chat || { log "aider install failed (needs Python 3.10-3.12)"; return; }
  fi
  local v; v=$("$venv/bin/aider" --version | awk '{print $NF}')
  local h; h=$(fresh_home aider); start_recorder aider "$h/project"
  local env=(OPENAI_API_BASE=$BASE/v1 OPENAI_API_KEY=dummy)
  local flags=(--model openai/gpt-4o --no-git --yes-always --no-check-update --analytics-disable --no-show-model-warnings
               --no-show-release-notes --no-fancy-input --no-pretty)
  run "$h" "$h/project" "${env[@]}" -- "$venv/bin/aider" "${flags[@]}" --message "$PROMPT1"
  run "$h" "$h/project" "${env[@]}" -- "$venv/bin/aider" "${flags[@]}" --restore-chat-history --message "$PROMPT2"
  stop_recorder; sanitize_all aider "$v"
}

opencode_like() {  # opencode_like <agent> <binary> <config-file-name> <version>
  local agent=$1 bin=$2 cfg=$3 v=$4
  local h; h=$(fresh_home "$agent"); start_recorder "$agent" "$h/project"
  cat > "$h/project/$cfg" <<EOF
{"autoupdate": false, "share": "disabled", "permission": {"bash": "allow", "edit": "allow"},
 "provider": {"openai": {"options": {"baseURL": "$BASE/v1", "apiKey": "dummy"}}}}
EOF
  local env=(XDG_CONFIG_HOME=$h/.config XDG_DATA_HOME=$h/.local/share XDG_CACHE_HOME=$h/.cache XDG_STATE_HOME=$h/.local/state
             OPENCODE_DISABLE_AUTOUPDATE=1 OPENCODE_DISABLE_MODELS_FETCH=1 OPENCODE_DISABLE_LSP_DOWNLOAD=1 OPENAI_API_KEY=dummy)
  run "$h" "$h/project" "${env[@]}" -- "$bin" run --model openai/gpt-4o "$PROMPT1"
  run "$h" "$h/project" "${env[@]}" -- "$bin" run --model openai/gpt-4o --continue "$PROMPT2"
  stop_recorder; sanitize_all "$agent" "$v"
}

cap_opencode() {
  npm_install opencode-ai opencode || return
  opencode_like opencode "$BIN/opencode" opencode.json "$("$BIN/opencode" --version)"
}

cap_kilocode() {
  npm_install @kilocode/cli kilocode || return
  opencode_like kilocode "$BIN/kilocode" kilo.json "$("$BIN/kilocode" --version | tail -1)"
}

cap_goose() {
  local gbin=$WORK/goose-bin
  if [ ! -x "$gbin/goose" ]; then
    curl -fsSL https://github.com/block/goose/releases/download/stable/download_cli.sh -o "$WORK/goose_download_cli.sh" \
      && CONFIGURE=false GOOSE_BIN_DIR=$gbin bash "$WORK/goose_download_cli.sh" >/dev/null 2>&1 || { log "goose install failed"; return; }
  fi
  local v; v=$("$gbin/goose" --version | awk '{print $NF}')
  local h; h=$(fresh_home goose); start_recorder goose "$h/project"
  local env=(GOOSE_DISABLE_KEYRING=1 GOOSE_TELEMETRY_ENABLED=false GOOSE_MODE=auto GOOSE_PROVIDER=openai GOOSE_MODEL=gpt-4o
             OPENAI_API_KEY=dummy OPENAI_HOST=$BASE)
  run "$h" "$h/project" "${env[@]}" -- "$gbin/goose" run --name probe -t "$PROMPT1"
  run "$h" "$h/project" "${env[@]}" -- "$gbin/goose" run --name probe --resume -t "$PROMPT2"
  stop_recorder; sanitize_all goose "$v"
  # second entry point: the same CLI speaking the Anthropic protocol
  h=$(fresh_home goose__anthropic); start_recorder goose__anthropic "$h/project"
  env=(GOOSE_DISABLE_KEYRING=1 GOOSE_TELEMETRY_ENABLED=false GOOSE_MODE=auto GOOSE_PROVIDER=anthropic GOOSE_MODEL=claude-sonnet-4-5
       ANTHROPIC_API_KEY=dummy ANTHROPIC_HOST=$BASE)
  run "$h" "$h/project" "${env[@]}" -- "$gbin/goose" run --name probe -t "$PROMPT1"
  run "$h" "$h/project" "${env[@]}" -- "$gbin/goose" run --name probe --resume -t "$PROMPT2"
  stop_recorder; sanitize_all goose "$v" cli-anthropic "$RAW/goose__anthropic"
}

cap_cline() {
  npm_install cline cline || return
  local v; v=$("$BIN/cline" --version | tail -1)
  local h; h=$(fresh_home cline); start_recorder cline "$h/project"
  local cfg=(--config "$h/.cline" --data-dir "$h/.cline/data")
  run "$h" "$h/project" -- "$BIN/cline" auth openai -k sk-dummy -b "$BASE/v1" -m gpt-4o "${cfg[@]}"
  # 3.0.73 has no headless resume (--id needs a TTY). The follow-up shape comes from Cline itself: after the
  # recorder's text answer it sends a "[SYSTEM] … call submit_and_exit" user turn, which the recorder answers
  # with a tool call and, once that result is back, with the completion tool.
  run "$h" "$h/project" -- "$BIN/cline" --yolo --json --retries 1 -t 120 "${cfg[@]}" -P openai -m gpt-4o "$PROMPT1"
  stop_recorder; sanitize_all cline "$v"
}

cap_openclaw() {
  npm_install openclaw openclaw || return
  local v; v=$("$BIN/openclaw" --version | awk '{print $2}')
  local h; h=$(fresh_home openclaw); start_recorder openclaw "$h/project"
  mkdir -p "$h/.openclaw"
  cat > "$h/.openclaw/openclaw.json" <<EOF
{"agents": {"defaults": {"model": {"primary": "recorder/probe-model"}, "workspace": "$h/project"}},
 "models": {"mode": "merge", "providers": {"recorder": {"baseUrl": "$BASE/v1", "apiKey": "dummy", "api": "openai-completions",
   "models": [{"id": "probe-model", "name": "Probe Model", "reasoning": false, "input": ["text"],
               "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}, "contextWindow": 128000, "maxTokens": 8192}]}}}}
EOF
  run "$h" "$h/project" -- "$BIN/openclaw" agent --local --session-id probe -m "$PROMPT1" --json
  run "$h" "$h/project" -- "$BIN/openclaw" agent --local --session-id probe -m "$PROMPT2" --json
  stop_recorder; sanitize_all openclaw "$v"
  # second entry point: the native openai provider with its base URL overridden. OpenClaw only adds its
  # attribution headers (originator, version) for native provider ids, so this is a second header variant.
  h=$(fresh_home openclaw__openai); start_recorder openclaw__openai "$h/project"
  mkdir -p "$h/.openclaw"
  cat > "$h/.openclaw/openclaw.json" <<EOF
{"agents": {"defaults": {"model": {"primary": "openai/gpt-4o"}, "workspace": "$h/project"}},
 "models": {"mode": "merge", "providers": {"openai": {"baseUrl": "$BASE/v1", "apiKey": "dummy"}}}}
EOF
  run "$h" "$h/project" -- "$BIN/openclaw" agent --local --session-id probe -m "$PROMPT1" --json
  run "$h" "$h/project" -- "$BIN/openclaw" agent --local --session-id probe -m "$PROMPT2" --json
  stop_recorder; sanitize_all openclaw "$v" cli-openai "$RAW/openclaw__openai"
  # third entry point: what a gateway sees on real api.openai.com traffic. OpenClaw adds its attribution headers
  # (originator, version, User-Agent) only when the openai provider's base URL names api.openai.com, so the base URL
  # keeps that hostname (on the recorder's port, so a miss can only hang) and a net.connect shim preloaded into the
  # OpenClaw process sends the connection to the recorder on 127.0.0.1 instead.
  cat > "$WORK/net-shim.cjs" <<'EOF'
// Preloaded into the agent process: TCP connections to api.openai.com go to the recorder on 127.0.0.1 instead.
const net = require("net");
const port = Number(process.env.AIFORENSICS_PORT || "18599");
for (const name of ["connect", "createConnection"]) {
  const orig = net[name];
  net[name] = function (...args) {
    const o = args[0];
    if (o && typeof o === "object" && !Array.isArray(o) && (o.host === "api.openai.com" || o.hostname === "api.openai.com")) {
      args[0] = Object.assign({}, o, { host: "127.0.0.1", hostname: "127.0.0.1", port });
    }
    return orig.apply(this, args);
  };
}
EOF
  h=$(fresh_home openclaw__native); start_recorder openclaw__native "$h/project"
  mkdir -p "$h/.openclaw"
  cat > "$h/.openclaw/openclaw.json" <<EOF
{"agents": {"defaults": {"model": {"primary": "openai/gpt-4o"}, "workspace": "$h/project"}},
 "models": {"mode": "merge", "providers": {"openai": {"baseUrl": "http://api.openai.com:$PORT/v1", "apiKey": "dummy"}}}}
EOF
  local env=(NODE_OPTIONS="--require $WORK/net-shim.cjs" AIFORENSICS_PORT=$PORT)
  run "$h" "$h/project" "${env[@]}" -- "$BIN/openclaw" agent --local --session-id probe -m "$PROMPT1" --json
  run "$h" "$h/project" "${env[@]}" -- "$BIN/openclaw" agent --local --session-id probe -m "$PROMPT2" --json
  stop_recorder; sanitize_all openclaw "$v" cli-native "$RAW/openclaw__native"
}

cap_crush() {
  npm_install @charmland/crush crush || return
  local v; v=$("$BIN/crush" --version | awk '{print $NF}'); v=${v#v}
  local h; h=$(fresh_home crush); start_recorder crush "$h/project"
  mkdir -p "$h/.config/crush"
  cat > "$h/.config/crush/crushrc" <<EOF
provider add recorder --type openai-compat --base-url "$BASE/v1" --api-key "dummy"
model add recorder/gpt-4o --name "gpt-4o" --context-window 128000 --default-max-tokens 4096
model large recorder/gpt-4o
model small recorder/gpt-4o
option provider-auto-update false
permissions allow bash view ls grep glob
EOF
  local env=(XDG_CONFIG_HOME=$h/.config XDG_DATA_HOME=$h/.local/share XDG_CACHE_HOME=$h/.cache
             CRUSH_DISABLE_METRICS=1 CRUSH_DISABLE_PROVIDER_AUTO_UPDATE=1)
  run "$h" "$h/project" "${env[@]}" -- "$BIN/crush" run -q "$PROMPT1"
  run "$h" "$h/project" "${env[@]}" -- "$BIN/crush" run -q --continue "$PROMPT2"
  stop_recorder; sanitize_all crush "$v"
}

semver() { grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1; }

cap_kimi_code() {
  npm_install @moonshot-ai/kimi-code kimi || return
  local v; v=$("$BIN/kimi" --version | semver)
  local h; h=$(fresh_home kimi_code); start_recorder kimi_code "$h/project"
  mkdir -p "$h/.kimi-code"
  cat > "$h/.kimi-code/config.toml" <<EOF
default_model = "probe"

[providers.probe]
type = "openai"
base_url = "$BASE/v1"
api_key = "dummy"

[models.probe]
provider = "probe"
model = "probe-model"
max_context_size = 131072
EOF
  local env=(KIMI_CODE_NO_AUTO_UPDATE=1)
  run "$h" "$h/project" "${env[@]}" -- "$BIN/kimi" -p "$PROMPT1"
  run "$h" "$h/project" "${env[@]}" -- "$BIN/kimi" -c -p "$PROMPT2"
  stop_recorder; sanitize_all kimi_code "$v"
}

cap_codebuddy() {
  npm_install @tencent-ai/codebuddy-code codebuddy || return
  local v; v=$("$BIN/codebuddy" --version | semver)
  local h; h=$(fresh_home codebuddy); start_recorder codebuddy "$h/project"
  mkdir -p "$h/.codebuddy"
  cat > "$h/.codebuddy/models.json" <<EOF
{"models": [{"id": "gpt-4o", "name": "gpt-4o", "url": "$BASE/v1/chat/completions", "apiKey": "dummy", "vendor": "openai"}],
 "availableModels": ["gpt-4o"]}
EOF
  run "$h" "$h/project" -- "$BIN/codebuddy" -p "$PROMPT1" -y --model gpt-4o --output-format json
  run "$h" "$h/project" -- "$BIN/codebuddy" -p "$PROMPT2" -y --model gpt-4o --output-format json --continue
  stop_recorder; sanitize_all codebuddy "$v"
}

cap_copilot() {  # BYOK: COPILOT_PROVIDER_BASE_URL needs no GitHub login; three wire protocols
  npm_install @github/copilot copilot || return
  local v; v=$("$BIN/copilot" --version | semver)
  copilot_pass cli openai "$BASE/v1" gpt-4o "" "$v"
  copilot_pass cli-anthropic anthropic "$BASE" claude-sonnet-4 "" "$v"
  copilot_pass cli-responses openai "$BASE/v1" gpt-5 responses "$v"
}
copilot_pass() {  # copilot_pass <entry> <provider-type> <base-url> <model> <wire-api|""> <version>
  local entry=$1 ptype=$2 url=$3 model=$4 wire=$5 v=$6
  local raw=copilot; [ "$entry" != cli ] && raw="copilot__${entry#cli-}"
  local h; h=$(fresh_home "$raw"); start_recorder "$raw" "$h/project"
  local env=(COPILOT_HOME=$h/.copilot XDG_CONFIG_HOME=$h/.config XDG_CACHE_HOME=$h/.cache XDG_DATA_HOME=$h/.local/share
             COPILOT_PROVIDER_BASE_URL=$url COPILOT_PROVIDER_TYPE=$ptype COPILOT_MODEL=$model COPILOT_PROVIDER_API_KEY=dummy
             COPILOT_OFFLINE=true COPILOT_AUTO_UPDATE=false)
  [ -n "$wire" ] && env+=(COPILOT_PROVIDER_WIRE_API=$wire)
  local flags=(--allow-all --disable-client-telemetry --no-auto-update)
  run "$h" "$h/project" "${env[@]}" -- "$BIN/copilot" -p "$PROMPT1" "${flags[@]}"
  run "$h" "$h/project" "${env[@]}" -- "$BIN/copilot" -p "$PROMPT2" "${flags[@]}" --continue
  stop_recorder; sanitize_all copilot "$v" "$entry" "$RAW/$raw"
}

py_venv() {  # py_venv <dir> <pip args...>: a Python 3.12 (or 3.11/3.10) venv with the packages installed
  local venv=$1; shift
  [ -x "$venv/bin/python" ] && return 0
  local py; py=$(command -v python3.12 || command -v python3.11 || command -v python3.10 || echo "$PY")
  "$py" -m venv "$venv" && timeout 900 "$venv/bin/pip" install -q "$@" || { log "pip install $* failed"; return 1; }
}

cap_openhands() {
  py_venv "$WORK/openhands-venv" openhands || return
  local v; v=$("$WORK/openhands-venv/bin/openhands" --version | semver)
  local h; h=$(fresh_home openhands); start_recorder openhands "$h/project"
  local env=(LLM_BASE_URL=$BASE/v1 LLM_MODEL=openai/gpt-4o LLM_API_KEY=dummy OPENHANDS_SUPPRESS_BANNER=1)
  local flags=(--headless --json --always-approve --override-with-envs)
  run "$h" "$h/project" "${env[@]}" -- "$WORK/openhands-venv/bin/openhands" "${flags[@]}" -t "$PROMPT1"
  local cid; cid=$(ls "$h/.openhands/conversations" 2>/dev/null | head -1)
  run "$h" "$h/project" "${env[@]}" -- "$WORK/openhands-venv/bin/openhands" "${flags[@]}" --resume "$cid" -t "$PROMPT2"
  stop_recorder; sanitize_all openhands "$v"
}

cap_forge() {
  npm_install forgecode forge || return
  local v; v=$("$BIN/forge" --version | semver)
  local h; h=$(fresh_home forge); start_recorder forge "$h/project"
  mkdir -p "$h/.forge"
  # provider login and the OPENAI_URL migration both need a tty, so the credentials file is written directly; the
  # model must be listed by GET /v1/models (the recorder lists "aiforensics")
  printf '[{"id": "openai_compatible", "auth_details": {"api_key": "dummy"}, "url_params": {"OPENAI_URL": "%s/v1"}}]\n' "$BASE" > "$h/.forge/.credentials.json"
  run "$h" "$h/project" -- "$BIN/forge" config set model openai_compatible aiforensics
  run "$h" "$h/project" -- "$BIN/forge" -p "$PROMPT1"
  local cid; cid=$(grep -oE 'Initialize [0-9a-f-]{36}' "$WORK/last_run.log" | head -1 | awk '{print $2}')
  run "$h" "$h/project" -- "$BIN/forge" --conversation-id "$cid" -p "$PROMPT2"
  stop_recorder; sanitize_all forge "$v"
  # second entry point: the Anthropic protocol
  h=$(fresh_home forge__anthropic); start_recorder forge__anthropic "$h/project"
  mkdir -p "$h/.forge"
  printf '[{"id": "anthropic_compatible", "auth_details": {"api_key": "dummy"}, "url_params": {"ANTHROPIC_URL": "%s/v1"}}]\n' "$BASE" > "$h/.forge/.credentials.json"
  run "$h" "$h/project" -- "$BIN/forge" config set model anthropic_compatible aiforensics
  run "$h" "$h/project" -- "$BIN/forge" -p "$PROMPT1"
  cid=$(grep -oE 'Initialize [0-9a-f-]{36}' "$WORK/last_run.log" | head -1 | awk '{print $2}')
  run "$h" "$h/project" -- "$BIN/forge" --conversation-id "$cid" -p "$PROMPT2"
  stop_recorder; sanitize_all forge "$v" cli-anthropic "$RAW/forge__anthropic"
}

cap_nanobot() {
  py_venv "$WORK/nanobot-venv" nanobot-ai || return
  local v; v=$("$WORK/nanobot-venv/bin/pip" show nanobot-ai | awk '/^Version/{print $2}')
  local h; h=$(fresh_home nanobot); start_recorder nanobot "$h/project"
  run "$h" "$h/project" -- "$WORK/nanobot-venv/bin/nanobot" onboard
  "$PY" - "$h/.nanobot/config.json" "$BASE" <<'EOF'
import json, sys
path, base = sys.argv[1], sys.argv[2]
cfg = json.load(open(path, encoding="utf-8"))
cfg.setdefault("providers", {}).setdefault("custom", {}).update({"apiBase": base + "/v1", "apiKey": "dummy"})
d = cfg.setdefault("agents", {}).setdefault("defaults", {})
d.update({"provider": "custom", "model": "aiforensics"}); d.setdefault("dream", {})["enabled"] = False
json.dump(cfg, open(path, "w", encoding="utf-8"), indent=1)
EOF
  run "$h" "$h/project" -- "$WORK/nanobot-venv/bin/nanobot" agent -m "$PROMPT1" -s probe --no-markdown
  run "$h" "$h/project" -- "$WORK/nanobot-venv/bin/nanobot" agent -m "$PROMPT2" -s probe --no-markdown
  stop_recorder; sanitize_all nanobot "$v"
}

cap_trae_agent() {  # open-source Trae Agent (bytedance/trae-agent); three providers plus an interactive follow-up
  local src=$WORK/trae-agent
  if [ ! -x "$src/.venv/bin/trae-cli" ]; then
    [ -d "$src" ] || git clone -q --depth 1 https://github.com/bytedance/trae-agent "$src" || { log "trae-agent clone failed"; return; }
    local py; py=$(command -v python3.12 || echo "$PY")
    if command -v uv >/dev/null; then (cd "$src" && uv sync -q --python "$py" --extra evaluation)
    else "$py" -m venv "$src/.venv" && timeout 900 "$src/.venv/bin/pip" install -q -e "$src[evaluation]"; fi
    [ -x "$src/.venv/bin/trae-cli" ] || { log "trae-agent install failed"; return; }
  fi
  local v; v=$(cd "$src" && git rev-parse --short HEAD)
  local tools='[bash, str_replace_based_edit_tool, sequentialthinking, task_done]'
  local params='max_tokens: 4096, temperature: 0.5, top_p: 1, top_k: 0, max_retries: 1, parallel_tool_calls: true'
  local pass
  for pass in cli:openai:gpt-4o cli-chat:openrouter:gpt-4o cli-anthropic:anthropic:claude-sonnet-4-20250514; do
    local entry=${pass%%:*} rest=${pass#*:} provider model; provider=${rest%%:*}; model=${rest#*:}
    local raw=trae_agent; [ "$entry" != cli ] && raw="trae_agent__${entry#cli-}"
    local h; h=$(fresh_home "$raw"); start_recorder "$raw" "$h/project"
    local url=$BASE/v1; [ "$provider" = anthropic ] && url=$BASE
    cat > "$h/trae_config.yaml" <<EOF
agents:
  trae_agent: {enable_lakeview: false, model: probe, max_steps: 4, tools: $tools}
model_providers:
  $provider: {api_key: dummy, provider: $provider, base_url: $url}
models:
  probe: {model_provider: $provider, model: $model, $params}
EOF
    local cmd=("$src/.venv/bin/trae-cli")
    local common=(--provider "$provider" --model "$model" --working-dir "$h/project" --config-file "$h/trae_config.yaml")
    run "$h" "$h/project" -- "${cmd[@]}" run "$PROMPT1" "${common[@]}" --console-type simple --max-steps 4
    if [ "$entry" = cli ]; then  # the only headless follow-up is the interactive mode fed from stdin
      printf '%s\n%s\n%s\n%s\nexit\n' "$PROMPT1" "$h/project" "$PROMPT2" "$h/project" > "$WORK/trae-stdin.txt"
      RUN_STDIN=$WORK/trae-stdin.txt run "$h" "$h/project" -- "${cmd[@]}" interactive --provider "$provider" --model "$model" --config-file "$h/trae_config.yaml" --max-steps 4
    fi
    stop_recorder; sanitize_all trae_agent "$v" "$entry" "$RAW/$raw"
  done
}

cap_grok_cli() {
  npm_install @vibe-kit/grok-cli grok || return
  local v; v=$(grep -m1 '"version"' "$NPM_PREFIX/lib/node_modules/@vibe-kit/grok-cli/package.json" | semver)
  local h; h=$(fresh_home grok_cli); start_recorder grok_cli "$h/project"
  local env=(GROK_API_KEY=dummy GROK_BASE_URL=$BASE/v1 GROK_MODEL=grok-code-fast-1)
  # 0.0.34 has no headless resume; the first turn and the tool turn are all a one-shot run gives
  run "$h" "$h/project" "${env[@]}" -- "$BIN/grok" -d "$h/project" -p "$PROMPT1"
  stop_recorder; sanitize_all grok_cli "$v"
}

cap_mistral_vibe() {
  py_venv "$WORK/vibe-venv" mistral-vibe || return
  local v; v=$("$WORK/vibe-venv/bin/pip" show mistral-vibe | awk '/^Version/{print $2}')
  local h; h=$(fresh_home mistral_vibe); start_recorder mistral_vibe "$h/project"
  mkdir -p "$h/.vibe"
  cat > "$h/.vibe/config.toml" <<EOF
active_model = "recorder-model"
enable_telemetry = false
enable_update_checks = false
enable_notifications = false
[[providers]]
name = "recorder"
api_base = "$BASE/v1"
api_key_env_var = "RECORDER_API_KEY"
api_style = "openai"
backend = "generic"
[[models]]
name = "gpt-4o"
provider = "recorder"
alias = "recorder-model"
EOF
  local env=(RECORDER_API_KEY=dummy MISTRAL_API_KEY=dummy)
  local flags=(--workdir "$h/project" --trust --yolo --output json)
  run "$h" "$h/project" "${env[@]}" -- "$WORK/vibe-venv/bin/vibe" "${flags[@]}" -p "$PROMPT1"
  run "$h" "$h/project" "${env[@]}" -- "$WORK/vibe-venv/bin/vibe" "${flags[@]}" -c -p "$PROMPT2"
  stop_recorder; sanitize_all mistral_vibe "$v"
}

cap_junie() {  # JetBrains Junie with --provider=litellm: no JetBrains login; the model must be a built-in name listed by GET /v1/model/info
  local jh=$WORK/junie-home   # the npm postinstall unpacks the jpackage app (bundled JRE) under $HOME/.local
  if [ ! -x "$jh/.local/bin/junie" ]; then
    mkdir -p "$jh"; HOME=$jh timeout 900 npm install -g --prefix "$NPM_PREFIX" @jetbrains/junie >/dev/null 2>&1 || { log "junie install failed"; return; }
  fi
  local v; v=$(HOME=$jh "$jh/.local/bin/junie" --version 2>/dev/null | semver)
  local h; h=$(fresh_home junie); (cd "$h/project" && git init -q)
  start_recorder junie "$h/project"
  local flags=(--skip-update-check --share-anonymous-statistics=false --provider=litellm --litellm-url=$BASE --litellm-api-key=dummy
               --model=gpt-5.4 --output-format=json)
  # HOME stays on the install: the shim resolves the runtime through $HOME/.local/share/junie (JUNIE_DATA is that
  # directory, not the session store). The Java app keeps sessions under the real user home (~/.junie), not $HOME.
  run "$jh" "$h/project" -- "$jh/.local/bin/junie" "${flags[@]}" --task "$PROMPT1"
  local sid; sid=$(grep -oE '"session_?[iI][dD]": *"[^"]+"' "$WORK/last_run.log" | head -1 | sed 's/.*: *"//; s/"//')
  [ -z "$sid" ] && sid=$(ls -t ~/.junie/sessions 2>/dev/null | head -1)
  run "$jh" "$h/project" -- "$jh/.local/bin/junie" "${flags[@]}" --resume --session-id="$sid" --task "$PROMPT2"
  stop_recorder; sanitize_all junie "$v"
}

cap_open_interpreter() {  # the legacy PyPI package; the current Codex-based CLI ships only through GitHub releases
  py_venv "$WORK/oi-venv" open-interpreter "setuptools<80" || return   # it imports pkg_resources, dropped by setuptools 80+
  local v; v=$("$WORK/oi-venv/bin/pip" show open-interpreter | awk '/^Version/{print $2}')
  local h; h=$(fresh_home open_interpreter); start_recorder open_interpreter "$h/project"
  local env=(LITELLM_LOCAL_MODEL_COST_MAP=True)
  printf '%s\n' "$PROMPT1" > "$WORK/oi-stdin.txt"
  RUN_STDIN=$WORK/oi-stdin.txt run "$h" "$h/project" "${env[@]}" -- "$WORK/oi-venv/bin/interpreter" --stdin -y -dt --api_base "$BASE/v1" --api_key dummy --model gpt-4o
  stop_recorder; sanitize_all open_interpreter "$v"
  # second entry point: the Python SDK, whose chat() keeps the conversation across calls
  h=$(fresh_home open_interpreter__sdk); start_recorder open_interpreter__sdk "$h/project"
  cat > "$WORK/oi-sdk-driver.py" <<EOF
from interpreter import interpreter
interpreter.llm.api_base = "$BASE/v1"
interpreter.llm.api_key = "dummy"
interpreter.llm.model = "gpt-4o"
interpreter.auto_run = True
interpreter.disable_telemetry = True
interpreter.chat("$PROMPT1", display=False)
interpreter.chat("$PROMPT2", display=False)
EOF
  run "$h" "$h/project" "${env[@]}" -- "$WORK/oi-venv/bin/python" "$WORK/oi-sdk-driver.py"
  stop_recorder; sanitize_all open_interpreter "$v" cli-sdk "$RAW/open_interpreter__sdk"
}

cap_mini_swe_agent() {
  py_venv "$WORK/mini-venv" mini-swe-agent mcp || return   # litellm imports mcp on every completion
  local v; v=$("$WORK/mini-venv/bin/pip" show mini-swe-agent | awk '/^Version/{print $2}')
  local h; h=$(fresh_home mini_swe_agent); start_recorder mini_swe_agent "$h/project"
  local env=(MSWEA_CONFIGURED=true MSWEA_COST_TRACKING=ignore_errors MSWEA_MODEL_API_KEY=dummy OPENAI_API_KEY=dummy)
  # one-shot only: no resume; a text answer triggers its format-error loop, which ends by itself after three rounds
  run "$h" "$h/project" "${env[@]}" -- "$WORK/mini-venv/bin/mini" -c mini.yaml -c model.model_name=openai/gpt-4o -c "model.model_kwargs.api_base=$BASE/v1" \
    -c model.model_kwargs.api_key=dummy -c model.model_kwargs.custom_llm_provider=openai -c model.cost_tracking=ignore_errors \
    -c agent.cost_limit=0 -t "$PROMPT1" -y --exit-immediately -o "$h/trajectory.json"
  stop_recorder; sanitize_all mini_swe_agent "$v"
}

cap_gptme() {
  py_venv "$WORK/gptme-venv" gptme || return
  local v; v=$("$WORK/gptme-venv/bin/pip" show gptme | awk '/^Version/{print $2}')
  local h; h=$(fresh_home gptme); start_recorder gptme "$h/project"
  local env=(OPENAI_BASE_URL=$BASE/v1 OPENAI_API_KEY=dummy)
  run "$h" "$h/project" "${env[@]}" -- "$WORK/gptme-venv/bin/gptme" -n -y --name probe -m local/gpt-4o "$PROMPT1"
  run "$h" "$h/project" "${env[@]}" -- "$WORK/gptme-venv/bin/gptme" -n -y --name probe -m local/gpt-4o -r "$PROMPT2"
  stop_recorder; sanitize_all gptme "$v"
}

cap_continue() {  # Continue CLI with a local config.yaml: no Hub login (removed in 1.5.x); openai and anthropic providers
  npm_install @continuedev/cli cn || return
  local v; v=$("$BIN/cn" --version | semver)
  continue_pass cli openai gpt-4o "$v"
  continue_pass cli-anthropic anthropic claude-sonnet-4-5 "$v"
}
continue_pass() {  # continue_pass <entry> <provider> <model> <version>
  local entry=$1 provider=$2 model=$3 v=$4
  local raw=continue; [ "$entry" != cli ] && raw="continue__${entry#cli-}"
  local h; h=$(fresh_home "$raw"); start_recorder "$raw" "$h/project"
  cat > "$h/project/config.yaml" <<EOF
name: recorder-assistant
version: 0.0.1
schema: v1
models:
  - name: recorder
    provider: $provider
    model: $model
    apiBase: $BASE/v1
    apiKey: dummy
    roles: [chat, edit, apply]
EOF
  local env=(CONTINUE_GLOBAL_DIR=$h/.continue)
  run "$h" "$h/project" "${env[@]}" -- "$BIN/cn" --config ./config.yaml --auto -p "$PROMPT1"
  run "$h" "$h/project" "${env[@]}" -- "$BIN/cn" --config ./config.yaml --auto -p --resume "$PROMPT2"
  stop_recorder; sanitize_all continue "$v" "$entry" "$RAW/$raw"
}

cap_pi() {  # Pi coding agent: provider api selectable (openai-completions, openai-responses, anthropic-messages, google-generative-ai)
  npm_install @mariozechner/pi-coding-agent pi || return
  local v; v=$(grep -m1 '"version"' "$NPM_PREFIX/lib/node_modules/@mariozechner/pi-coding-agent/package.json" | semver)
  pi_pass cli openai-completions "$BASE/v1" gpt-4o "$v"
  pi_pass cli-anthropic anthropic-messages "$BASE" claude-sonnet-4-5 "$v"
}
pi_pass() {  # pi_pass <entry> <api> <base-url> <model> <version>
  local entry=$1 api=$2 url=$3 model=$4 v=$5
  local raw=pi; [ "$entry" != cli ] && raw="pi__${entry#cli-}"
  local h; h=$(fresh_home "$raw"); start_recorder "$raw" "$h/project"
  mkdir -p "$h/.pi/agent"
  cat > "$h/.pi/agent/models.json" <<EOF
{"providers": {"recorder": {"baseUrl": "$url", "apiKey": "dummy", "api": "$api",
  "models": [{"id": "$model", "name": "$model", "reasoning": false, "input": ["text"], "contextWindow": 128000, "maxTokens": 16384}]}}}
EOF
  printf '{"defaultProvider": "recorder", "defaultModel": "%s"}\n' "$model" > "$h/.pi/agent/settings.json"
  run "$h" "$h/project" -- "$BIN/pi" --mode json -p "$PROMPT1"
  run "$h" "$h/project" -- "$BIN/pi" --mode json -c -p "$PROMPT2"
  stop_recorder; sanitize_all pi "$v" "$entry" "$RAW/$raw"
}

cap_zcode() {  # Z Code (Zhipu): the official open-source runtime, installed through the community npm wrapper zcode-app-cli
  npm_install zcode-app-cli zcode || return
  local v; v=$(grep -m1 '"version"' "$NPM_PREFIX/lib/node_modules/zcode-app-cli/package.json" | semver)
  zcode_pass cli openai-chat-completions "$BASE/v1" "$v"
  zcode_pass cli-anthropic anthropic-messages "$BASE" "$v"
}
zcode_pass() {  # zcode_pass <entry> <api-type> <base-url> <version>
  local entry=$1 api=$2 url=$3 v=$4
  local raw=zcode; [ "$entry" != cli ] && raw="zcode__${entry#cli-}"
  local h; h=$(fresh_home "$raw"); start_recorder "$raw" "$h/project"
  mkdir -p "$h/.zcode/v2" "$h/.zcode/cli"
  cp "$NPM_PREFIX/lib/node_modules/zcode-app-cli/setting.example.json" "$h/.zcode/cli/setting.json"
  cat > "$h/.zcode/v2/provider_config.json" <<EOF
{"schemaVersion": 1, "config": {
  "providerConfigRules": {"providerRules": [{"providerId": "custom", "providerName": "Custom provider", "config": {
    "group": "standard-personal", "access": {"type": "api-key", "apiKey": "dummy"},
    "api": {"type": "$api", "baseUrl": "$url"}, "personalModelIds": ["glm-5.3"]}}]},
  "modelConfigRules": {"providerModelRules": [], "manualProviderModelRules": []},
  "defaultModelSelection": {"providerId": "custom", "modelId": "glm-5.3"}}}
EOF
  local env=(ZCODE_DISABLE_UPDATE_CHECK=1)
  run "$h" "$h/project" "${env[@]}" -- "$BIN/zcode" --cwd "$h/project" --mode yolo -p "$PROMPT1"
  run "$h" "$h/project" "${env[@]}" -- "$BIN/zcode" --cwd "$h/project" --mode yolo -c -p "$PROMPT2"
  stop_recorder; sanitize_all zcode "$v" "$entry" "$RAW/$raw"
}

cap_shell_gpt() {
  py_venv "$WORK/sgpt-venv" shell-gpt click || return   # typer no longer pulls click, sgpt imports it
  local v; v=$("$WORK/sgpt-venv/bin/pip" show shell-gpt | awk '/^Version/{print $2}')
  local h; h=$(fresh_home shell_gpt); start_recorder shell_gpt "$h/project"
  mkdir -p "$h/.config/shell_gpt"
  cat > "$h/.config/shell_gpt/.sgptrc" <<EOF
OPENAI_API_KEY=dummy
API_BASE_URL=$BASE/v1
DEFAULT_MODEL=gpt-4o
OPENAI_USE_FUNCTIONS=true
OPENAI_FUNCTIONS_PATH=$h/.config/shell_gpt/functions
CACHE_PATH=$h/.cache/shell_gpt/cache
CHAT_CACHE_PATH=$h/.cache/shell_gpt/chat_cache
PRETTIFY_MARKDOWN=false
EOF
  run "$h" "$h/project" -- "$WORK/sgpt-venv/bin/sgpt" --install-functions
  run "$h" "$h/project" -- "$WORK/sgpt-venv/bin/sgpt" --no-cache --chat probe "$PROMPT1"
  run "$h" "$h/project" -- "$WORK/sgpt-venv/bin/sgpt" --no-cache --chat probe "$PROMPT2"
  run "$h" "$h/project" -- "$WORK/sgpt-venv/bin/sgpt" --no-cache -s --no-interaction "print ok"   # shell role, no tools
  stop_recorder; sanitize_all shell_gpt "$v"
}

cap_vecli() {  # Volcengine veCLI (Gemini CLI fork on chat completions); openai and ark auth types
  npm_install @volcengine/vecli vecli || return
  local v; v=$(grep -m1 '"version"' "$NPM_PREFIX/lib/node_modules/@volcengine/vecli/package.json" | semver)
  local h; h=$(fresh_home vecli); start_recorder vecli "$h/project"
  mkdir -p "$h/.ve"; echo '{"security": {"auth": {"selectedType": "openai"}}}' > "$h/.ve/settings.json"
  # 0.1.31 has no headless resume: the follow-up shape only exists in the interactive TUI
  run "$h" "$h/project" OPENAI_API_KEY=dummy OPENAI_BASE_URL=$BASE/v1 OPENAI_MODEL=probe-model -- "$BIN/vecli" --yolo -p "$PROMPT1"
  stop_recorder; sanitize_all vecli "$v"
  h=$(fresh_home vecli__ark); start_recorder vecli__ark "$h/project"
  mkdir -p "$h/.ve"; echo '{"security": {"auth": {"selectedType": "ark-api-key"}}}' > "$h/.ve/settings.json"
  run "$h" "$h/project" ARK_API_KEY=dummy ARK_BASE_URL=$BASE/v1 ARK_MODEL=probe-model -- "$BIN/vecli" --yolo -p "$PROMPT1"
  stop_recorder; sanitize_all vecli "$v" cli-ark "$RAW/vecli__ark"
}

cap_hermes() {
  py_venv "$WORK/hermes-venv" hermes-agent || return
  local v; v=$("$WORK/hermes-venv/bin/pip" show hermes-agent | awk '/^Version/{print $2}')
  local h; h=$(fresh_home hermes); start_recorder hermes "$h/project"
  mkdir -p "$h/.hermes"
  cat > "$h/.hermes/config.yaml" <<EOF
model:
  default: gpt-4o
  provider: custom
  base_url: $BASE/v1
  api_key: dummy
EOF
  run "$h" "$h/project" -- "$WORK/hermes-venv/bin/hermes" chat -q "$PROMPT1" --yolo
  local sid; sid=$(grep -oE '[0-9]{8}_[0-9]{6}_[0-9a-f]{6}' "$WORK/last_run.log" | head -1)
  [ -z "$sid" ] && sid=$(ls -t "$h/.hermes/sessions" 2>/dev/null | head -1 | sed 's/\.[a-z]*$//')
  run "$h" "$h/project" -- "$WORK/hermes-venv/bin/hermes" chat --resume "$sid" -q "$PROMPT2" --yolo
  stop_recorder; sanitize_all hermes "$v"
}

cap_pool() {  # Poolside pool CLI in standalone (BYOK) mode; the installer requires accepting Poolside's EULA, so it only runs when asked
  local ph=$WORK/pool-home
  if [ ! -x "$ph/.local/bin/pool" ]; then
    [ -n "${AIFORENSICS_ACCEPT_EULAS:-}" ] || { log "pool: set AIFORENSICS_ACCEPT_EULAS=1 to accept Poolside's EULA and install"; return; }
    mkdir -p "$ph" && curl -fsSL https://downloads.poolside.ai/pool/install.sh -o "$WORK/pool-install.sh" \
      && HOME=$ph POOL_INSTALL_ACCEPT_EULA=1 POOL_INSTALL_UPDATE_PATH=0 sh "$WORK/pool-install.sh" >/dev/null 2>&1 || { log "pool install failed"; return; }
  fi
  local v; v=$("$ph/.local/bin/pool" --version 2>/dev/null | semver)
  local h; h=$(fresh_home pool); start_recorder pool "$h/project"
  local env=(POOLSIDE_STANDALONE_BASE_URL=$BASE/v1 POOLSIDE_API_KEY=dummy POOLSIDE_STANDALONE_MODEL=aiforensics)
  local flags=(-o json --unsafe-auto-allow --sandbox disabled)
  run "$h" "$h/project" "${env[@]}" -- "$ph/.local/bin/pool" exec -p "$PROMPT1" "${flags[@]}"
  run "$h" "$h/project" "${env[@]}" -- "$ph/.local/bin/pool" exec --continue -p "$PROMPT2" "${flags[@]}"
  stop_recorder; sanitize_all pool "$v"
}

resanitize() {  # rebuild fixtures/<agent>/ from capture/raw/<agent>[__<entry>]/ after a sanitiser change, no recapture
  local agent=$1 raw entry
  for raw in "$RAW/$agent" "$RAW/${agent}__"*; do
    [ -f "$raw/VERSION" ] || continue
    entry=cli; [ "$raw" != "$RAW/$agent" ] && entry="cli-${raw##*__}"
    sanitize_all "$agent" "$(cat "$raw/VERSION")" "$entry" "$raw"
  done
}

for agent in $AGENTS; do
  log "#### $agent"
  rm -f "$ROOT/fixtures/$agent"/*-cli-*.json "$ROOT/fixtures/$agent"/*-cli-anthropic-*.json 2>/dev/null
  if [ -n "${AIFORENSICS_SANITIZE_ONLY:-}" ]; then resanitize "$agent"; else "cap_$agent"; fi
done
log "done; now run: $PY -m aiforensics.cli eval"
