# Steer text for the Repowise arm

The Repowise arm is steered: the agent is told to call a Repowise tool first.
The harness writes this text, verbatim, as the arm's user-level `CLAUDE.md`
(the same channel and the same words as the block `repowise init` writes into
a project's agent file). The bare arm gets no `CLAUDE.md`.

```
For any question about this codebase, call a Repowise tool first (get_answer for how/where/why questions, search_codebase for an identifier, get_context for callers or a file).
```

`run_paired.py` reads the fenced block above; edit it here and nowhere else.
