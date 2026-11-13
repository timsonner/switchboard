# Switchboard

Local operator console for one project at a time. You steer a desk conversation (the `grok` CLI). Worker runs are separate cards. Full transcripts stay on disk under `data/`. The desk prompt gets the standing desk note, the recent steering thread, a short run index, and the scoreboard.

Workers named in the server: `agy`, `opencode`, `copilot`, `codex`, `hermes`. The harness-offload dispatcher (`Invoke-HarnessOffload.ps1`) launches them.

A request for real work opens a **contest**: two implementers on git worktrees, then a third worker reviews both. You (or the desk) approve one tree or veto. **Apply** merges that worktree branch onto the project directory. **Revert apply** resets the project to the pre-apply commit. One writer per directory. Contest implementers can run together because each has its own worktree. A project that is not a git repo gets a single implementer in the project directory.

## Prerequisites

- Python 3, standard library only. This repo has no `requirements.txt` and no install step. Checked here with Python 3.12.3.
- The scripts `serve.cmd`, `serve.ps1`, `start-detached.ps1`, and `install-startup.ps1` are Windows PowerShell.
- `grok` on `PATH` if you want desk replies. If the CLI is missing, the message is still stored and the reply text says the CLI is not on `PATH`.
- The dispatcher script, or worker runs record `dispatcher missing`:
  - default: `../agent-skills/harness-offload/scripts/Invoke-HarnessOffload.ps1` (sibling of this repo)
  - or set `agent_skills` in `config.json`
  - or set `SWITCHBOARD_AGENT_SKILLS` to that agent-skills directory
- `git` on `PATH` for worktrees, the branch graph, apply, and revert.
- Optional `node` on `PATH`. After an apply on this repo, the server syntax-checks the script inside `ui/index.html`. If that check fails, the apply is rolled back.

Keep the bind on `127.0.0.1`. A second `server.py` exits if `data/server.pid` is still alive or if the port is already listening.

## Install, build, run, test

There is no package install and no build.

Optional config. Copy the example and edit it. `config.json` is gitignored.

```powershell
copy config.example.json config.json
```

```json
{ "agent_skills": "../agent-skills", "host": "127.0.0.1", "port": 8787, "token": "" }
```

An empty `token` means mutating requests are open on the loopback bind. If `token` is non-empty, or `SWITCHBOARD_TOKEN` is set, `POST`, `PATCH`, and `DELETE` need header `X-Switchboard-Token` or `Authorization: Bearer <token>`. `SWITCHBOARD_TOKEN` overrides the file. `SWITCHBOARD_AGENT_SKILLS` overrides `agent_skills`.

`serve.ps1` and `start-detached.ps1` look for Python in `%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe` first, then `python` on `PATH`. Both refuse to start when something is already listening on port 8787. The server itself reads `host` and `port` from config (default `127.0.0.1:8787`).

From this repo on Windows:

```powershell
.\serve.cmd
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\serve.ps1
```

Detached process (keeps running after the caller exits). Log: `data\server.log`.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-detached.ps1
```

Same server without PowerShell:

```powershell
python server.py
```

Open [http://127.0.0.1:8787](http://127.0.0.1:8787).

Health and worker list:

```powershell
curl http://127.0.0.1:8787/api/health
curl http://127.0.0.1:8787/api/workers
```

This repo has no test suite. Syntax-check the Python files:

```powershell
python -m py_compile server.py termconpty.py
```

`install-startup.ps1` registers a current-user logon task named `Switchboard`. The script header says to use it in production only, and not on a lab or dev box. Bind stays `127.0.0.1`.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-startup.ps1
Unregister-ScheduledTask -TaskName Switchboard -Confirm:$false
```

## Usage

1. Start the server and open [http://127.0.0.1:8787](http://127.0.0.1:8787).
2. Under **New**, type a project directory or use **Browse…**, then **Add project**. The id is a slug of the folder name. **Remove** (project menu) deletes Switchboard history for that id under `data/`. The project directory on disk stays in place.
3. **For Grok (desk)** is a standing note sent on every desk turn. **Save desk note** writes it. Workers can still read files in the project, including its README.
4. The composer **To** list is **Grok (desk)** or one worker. **allow tools** is passed through on a direct worker send. **Ctrl+Enter** or **Send** posts the message.
5. Desk (no worker selected): the server calls `grok` with `prompts/desk.txt`, the desk note, scores, open contests, the recent thread, and the run index. A contest or dispatch block in the reply starts runs. A verdict block records approve or veto. If the desk does not emit a contest for a work request, the server fills one from the scoreboard.
6. Direct worker: **Send** starts one run in the project directory. **reply** on a Team card resumes that worker session as a new card. **review** fills the composer with a review prompt for that run. **Resume last session** does the same for the selected worker.
7. Right rail:
   - **Desk**: you, grok, and system events. Buttons on an event open that run's diff or transcript.
   - **Team**: run cards (worker, status, summary). **transcript** and **diff** open the center pane. A run that exits non-zero but left a dirty git tree is marked `wrote files`.
   - **Contests**: while status is `awaiting_desk`, **approve reviewer pick**, **approve** a run, or **veto all**. After `approved`, **apply** merges branch `sb/<worker>-<run>` with `--no-ff`. Uncommitted project files are stashed and popped back on top. **revert apply** hard-resets to the pre-apply commit and restores that stash.
   - **Scores**: per-worker wins, vetoes, done, and error. Click a card to select that worker in **To**.
8. Center pane: **Diff**, **Transcript**, **Worktrees**, **Graph** (git log of the project), **Terminal** (PowerShell, cmd, or Git Bash, rooted at the project or a contest worktree). The header shows dispatcher, grok, and process id. If files on disk are newer than the process, restart with `start-detached.ps1` or `python server.py`.

`data/` is local and gitignored. Shape of `project.json`, `thread.jsonl`, runs, contests, and scores: [docs/data-model.md](docs/data-model.md).

## Layout

| Path | Role |
|---|---|
| [server.py](server.py) | Local HTTP API and static UI |
| [termconpty.py](termconpty.py) | Windows ConPTY helper (ctypes only). The in-page terminal in `server.py` starts a piped shell |
| [ui/index.html](ui/index.html) | Console UI |
| [ui/vendor/xterm/](ui/vendor/xterm/) | Vendored xterm.js used by the Terminal tab |
| [prompts/desk.txt](prompts/desk.txt) | Instructions included in each desk prompt |
| [docs/data-model.md](docs/data-model.md) | `project`, `thread`, `run`, `contest`, `scores` |
| [config.example.json](config.example.json) | Sample `config.json` |
| [serve.cmd](serve.cmd) | Windows launcher for `serve.ps1` |
| [serve.ps1](serve.ps1) | Windows launcher for `server.py` |
| [start-detached.ps1](start-detached.ps1) | Start `server.py` outside the calling console |
| [install-startup.ps1](install-startup.ps1) | Optional logon scheduled task |
| `data/` | Runtime store (gitignored) |

## License

No license file in this repo.
