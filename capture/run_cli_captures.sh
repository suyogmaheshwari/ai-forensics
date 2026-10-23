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
ALL_AGENTS="claude_code codex qwen_code gemini_cli aider opencode goose cline kilocode openclaw"
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
  "$PY" -m aiforensics.cli record --out "$RAW/$1" --port $PORT --reply tool-call --work-dir "$2" --max-requests 16 \
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
  (cd "$project" && timeout 180 env -i PATH="$CLEAN_PATH" HOME="$home" TERM=dumb NO_COLOR=1 "${envs[@]}" "$@" </dev/null) \
    >"$WORK/last_run.log" 2>&1
  log "exit $? : $(tail -c 300 "$WORK/last_run.log" | tr '\n' ' ')"
}

sanitize_all() {  # sanitize_all <agent> <version> [entrypoint] [raw-dir]; numbering continues after existing fixtures
  local agent=$1 version=$2 entry=${3:-cli} raw=${4:-$RAW/$1} line n
  mkdir -p "$ROOT/fixtures/$agent"
  printf '%s\n' "$version" > "$raw/VERSION"
  n=$(ls "$ROOT/fixtures/$agent" | grep -c '\.json$')
  for f in "$raw"/req_*.json; do
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
