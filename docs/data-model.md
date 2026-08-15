# Switchboard data model

One home for the facts. The UI and any later API read this; they do not
invent parallel fields.

Paths are under `data/` on the machine that runs the console (not in git).

```text
data/
  projects/<project-id>/
    project.json
    thread.jsonl          # you ↔ Grok only
    runs/<run-id>/
      run.json            # the card
      transcript.md       # or raw.jsonl from the worker
      offload.json        # dispatcher result (session_id, exit, stdout)
```

## project

| Field | Meaning |
|---|---|
| `id` | Stable slug |
| `dir` | Workspace path (the harness `-Dir`) |
| `brief` | Short project brief (the only long-ish text always eligible for Grok) |
| `created_at` | ISO-8601 |

One project ↔ one `dir`. Do not point two projects at the same tree if both
will run writers.

## thread (orchestrator)

JSONL, one object per event, **you ↔ Grok only**.

| Field | Meaning |
|---|---|
| `ts` | ISO-8601 |
| `role` | `user` \| `grok` \| `system` |
| `text` | Steering text |
| `run_ids` | Optional runs this turn started or referenced |

Grok’s live context = `project.brief` + last N thread events + the **run
index** (cards), not `transcript.md`.

## run (card)

| Field | Meaning |
|---|---|
| `id` | Switchboard run id (not the worker session id) |
| `project_id` | Parent |
| `worker` | `agy` \| `opencode` \| `copilot` \| `codex` \| `hermes` |
| `session_id` | Worker session / conversation / thread id from the dispatcher |
| `dir` | Copied from the project (must match) |
| `status` | `queued` \| `running` \| `done` \| `error` |
| `summary` | ≤ 3 sentences, written after the run (or a stub while running) |
| `prompt` | What we sent |
| `parent_run_id` | Prior card in this worker session, if this is a reply |
| `allow_tools` | bool |
| `started_at` / `ended_at` | ISO-8601 |
| `transcript_path` | Relative to the run dir |
| `offload_path` | Dispatcher JSON |

Follow-up on a card: same `worker` + `dir` + `session_id`. Always a **new
card** with `parent_run_id` pointing at the card you replied on. Do not
append to the old `transcript.md`.

## What never goes into Grok’s window by default

- Full worker JSONL / TUI dumps
- Entire `thread.jsonl` older than the steering tail
- Secrets from worker env

Pull those with an explicit read (“open the Codex P1”, “last 40 lines”).
