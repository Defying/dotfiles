# Agent Context Handoff

Use `handoff` whenever an agent session is getting low on context or usage
limits, or when work should move between Codex and Claude Code.

The rule is simple: write a Markdown report first, then hand the next agent a
compact `/goal` prompt that tells it to read that exact report.

## Command

```sh
handoff --title "Home Assistant investigation" \
  --goal "Continue investigating Home Assistant on ben@orange.local." \
  --next "Resume from Docker/API inspection; keep secrets redacted." \
  --note "SSH works as ben@orange.local; Home Assistant runs in Docker."
```

The command writes `docs/agent-handoff-*.md`, prints the `/goal` prompt, and
copies it to the clipboard when Wayland clipboard tooling is available.

`agent-handoff` is the full command name; `handoff` is a short wrapper.

## Report Requirements

Each generated report includes:

- current objective
- next actions
- context notes
- validation to re-run
- guardrails
- machine snapshot
- git branch, status, recent commits, and diff stat
- a paste-ready `/goal` prompt

Before handing off, edit the generated report enough that the next agent can
continue without relying on hidden chat history.

## Prompt Shape

The generated prompt intentionally stays compact:

```text
/goal Read /absolute/path/to/report.md in full before doing anything. Continue
the work described under Current Objective and Next Actions...
```

The next agent should update the same report as work changes. Before it runs
low on context or limits, it should write a refreshed Markdown report and leave
a new `/goal` prompt that points to the refreshed file.
