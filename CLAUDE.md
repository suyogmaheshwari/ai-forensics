# Working in this repository

ai-forensics identifies which AI coding agent sent an LLM API request. Read `README.md` for the
three-layer model and `AGENTS.md` for the target list, capture method per agent and the
definition of done. This file is the standing brief for any agent session working here.

## Standing task

1. **Keep `AGENTS.md` complete.** Using your own knowledge plus the npm and PyPI registries (and
   the web when the environment allows it), add every AI coding agent that can be pointed at a
   custom model endpoint: CLI tools and IDEs. For each: install command, the setting that changes
   its base URL, the protocol family it speaks, whether it runs headless, and a status.
2. **Capture every agent that runs headless, across a whole session, not just the first
   request.** Install it, start the recorder
   (`python3 -m aiforensics.cli record --out capture/raw/<agent>`), point the agent at
   `http://127.0.0.1:18599` (append `/v1` where the agent expects it) with a dummy key, and
   capture at least three request shapes:
   - the first turn of a fresh session;
   - a turn that follows a tool call (ask for something that needs a file read or a shell
     command; with a dead-end recorder, use the replay helper or a canned tool-call response so
     the agent sends its tool-result turn);
   - a follow-up user message in the same session, and the agent's retry after an error if it
     retries.
   Also capture each entry point the agent has (CLI vs IDE extension vs SDK mode) when they
   differ. If an agent refuses to start without a login or a real-looking key, note the
   obstacle in `AGENTS.md` and move on.
3. **Make one fixture per captured shape.** `python3 -m aiforensics.cli sanitize <raw> --agent
   <agent> --version <v> -o fixtures/<agent>/<nnn>-<entrypoint>-<turn>.json` (for example
   `001-cli-first-turn.json`, `002-cli-after-tool-call.json`, `003-cli-followup.json`). Open the result and confirm it contains no
   credentials, ids, paths, emails or conversation text. Never commit anything under
   `capture/raw/`.
4. **Write the rule** under `rules/<agent>.json` using at least two feature groups, and a deny
   feature wherever another product shares a layer. A signature must be **consistent across the
   session**: every fixture of the agent, first turn, after a tool call and follow-up, must be
   identified by the same rule. Prefer features that survive the whole session (product headers,
   the first clause of the hard-coded prompt, the core tool-name set) over anything that only
   appears on the first request or in one version. Record in the rule's `notes` field which
   features were observed to vary between turns or versions.
5. **Prove it.** `python3 -m aiforensics.cli eval` must show precision 1.0 and recall 1.0 over
   all fixtures, and the tests must pass. Add a test when you add an operator or change scoring.
6. **Open one pull request per batch** with the new fixtures, rules, tests and the `AGENTS.md`
   rows updated. Desktop IDEs stay `todo`; they are captured on a workstation by a person.

## Conventions

- Python 3.9 compatible, standard library only; no new dependencies without a reason in the PR.
- Run the CLI as `python3 -m aiforensics.cli …`; installing the package is optional.
- Rule operators are documented in `aiforensics/rules.py`; extend there, not in rule files.
- Keep fixtures small: the sanitiser already truncates texts to 200 characters.
- Commit messages: one line, imperative, what changed and for which agent or version.
- Do not add a co-author trailer.
