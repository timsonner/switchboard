# Switchboard data model

One home for the facts. The UI and any later API read this; they do not
invent parallel fields.

Paths are under `data/` on the machine that runs the console (not in git).

```text
data/
  projects/<project-id>/
    project.json
    thread.jsonl          # you ↔ Grok only
    scores.json           # per-worker wins / vetoes / done / error
    contests/<contest-id>.json
    worktrees/<worker>-<run-id>/   # git worktree, isolated writer
    runs/<run-id>/
      run.json
      transcript.md
      offload.json
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
| `dir` | Writer cwd (project dir, or a contest worktree) |
| `contest_id` | Set when this run is part of a contest |
| `status` | `queued` \| `running` \| `done` \| `error` |
| `role` | Optional label (`implement`, `review`, …) |
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

## contest

Two implementers produce isolated trees. A third worker reviews both.
Desk (Grok) approves one winner or vetoes. The human can override.

| Field | Meaning |
|---|---|
| `id` | Contest id |
| `goal` | Bounded task |
| `status` | `competing` \| `reviewing` \| `awaiting_desk` \| `approved` \| `vetoed` \| `error` |
| `implementer_run_ids` | Candidate cards |
| `review_run_ids` | Peer-review cards |
| `reviewer` | Worker that critiques (must not be an implementer if possible) |
| `winner_run_id` | Set on approve |
| `reason` | Desk / operator rationale |

One writer per **directory**. Contest implementers use worktrees so they
can run at the same time. No worktree (not a git repo) → single implementer.

## scores

`wins` increment on desk/operator approve. `vetoes` when that worker's
tree is rejected or the whole contest is vetoed. Used to pick the next team.

## What never goes into Grok’s window by default

- Full worker JSONL / TUI dumps
- Entire `thread.jsonl` older than the steering tail
- Secrets from worker env

Pull those with an explicit read (“open the Codex P1”, “last 40 lines”).
