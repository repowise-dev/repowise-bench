# INVALID: preflight rows, kept per standing rule 3

Five rows produced while the Codex runner was being brought up. Not deleted,
not comparable to anything, and moved out of the results directory only
because `run_experiment.py --resume` globs `*.jsonl` there and treated them as
completed work.

Each row is a bug worth keeping:

1-2. `codex produced no agent_message`. `codex.cmd` is a batch shim and a
   NEWLINE in a positional argument terminates the command line at cmd.exe.
   The prompt was truncated at its first blank line and every flag after it
   was discarded, so `--json` and `--cd` never applied: the agent answered a
   question it had not been asked, from `repowise-bench` rather than from the
   arm's worktree, in a human-readable stream. Fixed by feeding the prompt on
   stdin (`codex exec -`).

3. `Error loading config.toml: invalid type: string ... expected a map`, for a
   value that came off the command line and not from any config.toml. `-c`
   parses its value as TOML and a JSON object is not a TOML inline table.

4. c0-bare, clean, `judge_failed: ` with an empty message.

5. repowise, `error: null`, `arm_exercised: true`, one `get_answer` issued,
   judge 9.0/10, cost $0.074 — and the call itself returned
   `user cancelled MCP tool call`, in a run with no user in it. Codex's
   non-interactive approval policy is DENY. The agent was silently refused,
   fell back to the shell, and answered well. That row is finding D1 in Codex
   spelling and it is the reason `arm_exercised` now requires a call the
   server actually ANSWERED, not merely one the agent issued.
