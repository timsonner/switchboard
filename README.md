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

## Run

Needs Python 3, `grok` on PATH (desk replies), and
`../agent-skills/harness-offload/scripts/Invoke-HarnessOffload.ps1` (or set
`SWITCHBOARD_AGENT_SKILLS`).

```powershell
# from this repo
.\serve.cmd
# or, from another process (survives a closing chat):
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-detached.ps1
# open http://127.0.0.1:8787  (not localhost)
```

Do **not** auto-start at logon yet. `install-startup.ps1` is kept for later.

Keep the bind on `127.0.0.1`. Optional write-token: set `token` in `config.json`
or `SWITCHBOARD_TOKEN`. Mutating API calls then need `X-Switchboard-Token`.
A second `server.py` is refused (pid lock). After **Apply**, uncommitted work
is restored on top of the merge; a broken `ui/index.html` rolls the apply back.

Add a project directory, talk to **Grok (desk)** or pick a worker and Send.
Desk opens a **contest** for real work: two implementers on git worktrees, a
third worker reviews both, then the desk approves one tree or vetoes. Scores
steer the next lineup. You can override the verdict in the UI. **Apply
winner** merges that tree onto the project; **Revert apply** rolls back.
Worker runs are cards; open a card to read the transcript (that text is **not**
injected into Grok). **Reply on this run** resumes the same worker session as a
new card. `data/` is local and gitignored.

Optional `config.json` in this repo root (see [config.example.json](config.example.json)):

```json
{ "agent_skills": "../agent-skills", "host": "127.0.0.1", "port": 8787, "token": "" }
```

## Layout on disk

| Path | Role |
|---|---|
| [docs/data-model.md](docs/data-model.md) | `project` / `thread` / `run` |
| [server.py](server.py) | Local HTTP API + static UI |
| [ui/index.html](ui/index.html) | Three panes |
| [prompts/desk.txt](prompts/desk.txt) | Grok front-desk instructions |
| `data/` | Runtime transcripts + cards |

## Name

**Switchboard** — you plug a line, you can listen, you do not merge every
conversation into one head. Short to type. Not a social network.

## Rules

- One writer per project dir.
- Workers do not spawn other workers.
- Store everything. Show everything. Feed Grok the index.
