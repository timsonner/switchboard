# Switchboard

Local operator console for **you + Grok + harness workers**.

Not Buzz. Not the Hermes gateway. Those are messaging transports. This is the
system of record for a project: one orchestrator thread, many worker **runs**,
full transcripts on disk. You can always read the chats. Grok only loads
indexes, not the whole pile.

Workers today: `agy`, `opencode`, `copilot`, `codex`, `hermes` via
[`agent-skills` / `harness-offload`](../agent-skills/harness-offload/SKILL.md).

## Layout

```text
┌────────────┬─────────────────────────────┬──────────────────┐
│ projects   │  Grok (front desk)          │  run cards       │
│ (dirs)     │  steering, pick worker,     │  worker · id ·   │
│            │  “resume that Codex run”    │  status · 3-line │
│            │                             │  summary         │
│            │                             │  [open log]      │
└────────────┴─────────────────────────────┴──────────────────┘
```

- **Center** is the only conversation that lives in the model context.
- **Cards** are jobs (`Invoke-HarnessOffload`). Click a card to read the
  full transcript in the UI — that does **not** get pasted back into Grok.
- Direct reply on a card is `-Session` on the same worker + dir.

## Kickoff (this repo)

| Path | Role |
|---|---|
| [docs/data-model.md](docs/data-model.md) | `project` / `thread` / `run` — one home for the schema |
| [ui/index.html](ui/index.html) | Static wireframe of the three panes |
| `data/` | Local runtime (gitignored). Transcripts + run cards. |

Next slice (not done): a tiny local server that appends to the Grok thread,
shells out to `Invoke-HarnessOffload.ps1`, and writes `data/runs/<id>/`.

## Name

**Switchboard** — you plug a line, you can listen, you do not merge every
conversation into one head. Short to type. Not a social network.

## Rules

- One writer per project dir.
- Workers do not spawn other workers.
- Store everything. Show everything. Feed Grok the index.
