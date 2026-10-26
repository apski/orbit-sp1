---
name: codex-agent
description: >
  Hand a self-contained task to a Codex CLI agent (OpenAI models: Luna, Astra, Sol, Terra) and get its answer back as
  a file: `codex exec` non-interactive, read-only by default, optional live web search, run in the background. Use
  when the user says "use codex", "ask codex", "codex agent", "a luna agent", "astra agent", "get a second opinion
  from codex", "run this through codex", or wants an independent model to research or review something.
last-updated: 2026-10-26
---

# codex-agent: one prompt in, one answer file out

```bash
OUT=<scratchpad>/codex-<topic>.md
codex [--search] exec -m gpt-5.6-luna -s read-only -C <dir it should read> --skip-git-repo-check --ephemeral \
  -c model_reasoning_effort=high -o "$OUT" - < prompt.md > "$OUT.log" 2>&1
```

Run it with the Bash tool's `run_in_background`; you are notified when it exits. Then read `$OUT` (the final
message only); the `.log` has the whole transcript if the answer looks off.

## Choices

| Flag | Pick |
|---|---|
| `-m` | Luna = `gpt-5.6-luna` (`gpt-6-luna` is in the catalogue but rejected for a ChatGPT-account login: "not supported when using Codex with a ChatGPT account", 2026-10-26). `gpt-6-astra` is the config default. Probe a model first: `echo 'Reply ok' \| codex exec -m <id> -s read-only --skip-git-repo-check --ephemeral -c model_reasoning_effort=low -` |
| `-s` | **always pass it.** The user's config defaults to `danger-full-access` + `approval_policy = "never"`, so without `-s read-only` Codex can edit and run anything. Use `workspace-write` only when the task is to change files in `-C`, and say so. |
| `-C` | the directory it should read; everything it cites is relative to it |
| `--search` | live web search, for "is this upstream?", release notes, docs. A top-level flag: `codex --search exec ...` (after `exec` it is rejected) |
| `-c model_reasoning_effort=` | config default is `xhigh`; `medium`/`high` is faster for reading and classifying |
| `--ephemeral` | don't save a session file |
| `--output-schema f.json` | when you want structured JSON back |

## The prompt

Codex knows nothing of this conversation. Write the prompt to a file and pipe it in (no quoting problems):
the goal, where to look (paths), what is already known, the exact output shape (a table, a word limit), and "cite
file:line or URL for every claim; say 'unknown' rather than guess".

## Using the answer

It is another model's claims, not facts: spot-check two or three citations before passing them on, and say in the
reply that the analysis came from Codex (model name). Untrusted repos stay untrusted: read-only, no installs, no
scripts from them.
