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
| `brief` | Optional standing note for the **desk** (Grok), not a user-facing README |
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

Grok’s live context = `project.brief` (desk note) + last N thread events +
the **run index**. Not `transcript.md`. Not the README unless a worker opens it.

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
| `applied` | Winner tree has been merged onto `project.dir` |
| `apply` | Snapshot used to revert (`pre_head`, optional `stash_ref`) |

Approve does **not** change the project tree. **Apply winner** merges the
winning worktree branch (`sb/<worker>-<run>`) into `project.dir` (not a
rebase). Live dirty files are stashed, then **popped back on top** of the
merge so in-progress UI/server work is not silently lost. **Revert apply**
hard-resets to `pre_head` and restores that stash. If `server.py` changed,
restart the process — disk and the running API otherwise diverge.

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
