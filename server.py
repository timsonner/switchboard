"""Switchboard local server: static UI + project/thread/run API."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "ui"
DATA_DIR = ROOT / "data"
PROMPTS = ROOT / "prompts"
DEFAULT_SKILLS = (ROOT.parent / "agent-skills").resolve()
WORKERS = ("agy", "opencode", "copilot", "codex", "hermes")

_lock = threading.Lock()
STARTED_AT = time.time()


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _code_is_stale() -> bool:
    try:
        return Path(__file__).stat().st_mtime > STARTED_AT + 1
    except OSError:
        return False


def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return s[:64] or "project"


def list_directory(raw: str) -> dict:
    """List subfolders for the in-page picker. No GUI, no extra libraries."""
    raw = (raw or "").strip()
    if os.name == "nt" and raw in ("", "/", "\\"):
        dirs = [{"name": "Home", "path": str(Path.home())}]
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{letter}:\\"
            if Path(root).exists():
                dirs.append({"name": root, "path": root})
        return {"path": "", "parent": None, "dirs": dirs}
    if not raw:
        raw = str(Path.home())
    p = Path(raw)
    if not p.exists() or not p.is_dir():
        raise ValueError("not a directory")
    p = p.resolve()
    dirs = []
    try:
        children = list(p.iterdir())
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    for child in sorted(children, key=lambda c: c.name.lower()):
        try:
            if child.is_dir():
                dirs.append({"name": child.name, "path": str(child)})
        except OSError:
            continue
    parent: str | None
    if os.name == "nt" and len(p.parts) == 1:
        parent = ""
    elif p.parent != p:
        parent = str(p.parent)
    else:
        parent = None
    return {"path": str(p), "parent": parent, "dirs": dirs}


def refresh_path() -> None:
    if os.name != "nt":
        return
    try:
        machine = subprocess.check_output(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "[Environment]::GetEnvironmentVariable('Path','Machine')",
            ],
            text=True,
        ).strip()
        user = subprocess.check_output(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "[Environment]::GetEnvironmentVariable('Path','User')",
            ],
            text=True,
        ).strip()
        os.environ["Path"] = machine + ";" + user
    except Exception:
        pass


def load_config() -> dict:
    cfg = {
        "agent_skills": str(DEFAULT_SKILLS),
        "host": "127.0.0.1",
        "port": 8787,
    }
    path = ROOT / "config.json"
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    env_skills = os.environ.get("SWITCHBOARD_AGENT_SKILLS")
    if env_skills:
        cfg["agent_skills"] = env_skills
    skills = Path(cfg["agent_skills"])
    if not skills.is_absolute():
        skills = (ROOT / skills).resolve()
    cfg["agent_skills"] = str(skills)
    env_token = (os.environ.get("SWITCHBOARD_TOKEN") or "").strip()
    if env_token:
        cfg["token"] = env_token
    else:
        cfg["token"] = str(cfg.get("token") or "").strip()
    return cfg


def configured_token() -> str:
    return str(load_config().get("token") or "").strip()


def dispatcher_path(cfg: dict) -> Path:
    return (
        Path(cfg["agent_skills"])
        / "harness-offload"
        / "scripts"
        / "Invoke-HarnessOffload.ps1"
    )


def project_dir(pid: str) -> Path:
    return DATA_DIR / "projects" / pid


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def list_projects() -> list:
    root = DATA_DIR / "projects"
    if not root.exists():
        return []
    items = []
    for p in sorted(root.iterdir()):
        meta = p / "project.json"
        if meta.exists():
            items.append(read_json(meta, {}))
    return items


def load_project(pid: str) -> dict | None:
    path = project_dir(pid) / "project.json"
    if not path.exists():
        return None
    return read_json(path, None)


def list_runs(pid: str) -> list:
    runs = project_dir(pid) / "runs"
    if not runs.exists():
        return []
    cards = []
    for d in runs.iterdir():
        card = d / "run.json"
        if card.exists():
            cards.append(read_json(card, {}))
    cards.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return cards


def load_run(pid: str, rid: str) -> dict | None:
    path = project_dir(pid) / "runs" / rid / "run.json"
    if not path.exists():
        return None
    return read_json(path, None)


def find_run(rid: str) -> tuple[str, dict] | None:
    root = DATA_DIR / "projects"
    if not root.exists():
        return None
    for p in root.iterdir():
        card = p / "runs" / rid / "run.json"
        if card.exists():
            return p.name, read_json(card, {})
    return None


def latest_sessions(pid: str) -> dict:
    out: dict = {}
    for r in list_runs(pid):
        worker = r.get("worker")
        sid = r.get("session_id")
        if worker and sid and worker not in out:
            out[worker] = {"session_id": sid, "run_id": r.get("id"), "status": r.get("status")}
    return out


def exit_code_of(result: dict) -> int:
    raw = result.get("exit_code", 1)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1


def git_has_changes(directory: str) -> bool:
    if not directory or not is_git_repo(directory):
        return False
    try:
        proc = subprocess.run(
            ["git", "-C", directory, "status", "--porcelain"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except Exception:
        return False
    return bool((proc.stdout or "").strip())


def classify_run(result: dict, directory: str) -> tuple[str, str, int]:
    """Return (status, outcome, exit_code).

    Workers like AGY often exit 1 / empty stdout after writing a tree.
    That is mixed success, not a clean failure.
    """
    code = exit_code_of(result)
    dirty = git_has_changes(directory)
    if code == 0:
        return "done", "ok", code
    if dirty:
        return "done", "wrote_files", code
    return "error", "failed", code


def which_grok() -> str | None:
    refresh_path()
    return shutil.which("grok")


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
HERMES_SID_RE = re.compile(r"(20\d{6}_\d{6}_[0-9a-f]+)", re.I)


def clean_text(text: str) -> str:
    text = ANSI_RE.sub("", text or "")
    return text.replace("\u250a", " ").replace("\x00", "").strip()


def summarize(stdout: str, stderr: str) -> str:
    blob = clean_text(stdout) or clean_text(stderr)
    candidates = []
    for line in blob.splitlines():
        line = line.strip()
        if not line or line.startswith("@@") or line.startswith("a/") or line.startswith("b/"):
            continue
        if "NativeCommandError" in line or line.startswith("At C:"):
            continue
        low = line.lower()
        if low in ("review diff", "review", "diff", "┊ review diff", "review and diff"):
            continue
        candidates.append(line[:200])
    if candidates:
        return candidates[0]
    return blob[:200] or "no output"


def git_worktree_diff(directory: str, limit: int = 20000) -> str:
    if not is_git_repo(directory):
        return "(not a git worktree)"
    chunks = []
    for args in (
        ["git", "-C", directory, "status", "-sb"],
        ["git", "-C", directory, "diff", "--no-color"],
    ):
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
            if proc.stdout:
                chunks.append(proc.stdout)
            if proc.stderr and proc.stderr.strip():
                chunks.append(proc.stderr)
        except Exception as exc:
            chunks.append(str(exc))
    text = clean_text("\n".join(chunks)).strip()
    if not text:
        return "(no diff)"
    if len(text) > limit:
        text = text[:limit] + "\n…(truncated)"
    return text


def discover_hermes_session(directory: str) -> str | None:
    refresh_path()
    official = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "hermes-agent" / "venv" / "Scripts" / "hermes.exe"
    exe = str(official) if official.exists() else "hermes"
    try:
        proc = subprocess.run(
            [exe, "sessions", "list", "--workspace", directory, "--limit", "5"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except Exception:
        return None
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    found = HERMES_SID_RE.findall(text)
    return found[0] if found else None


DISPATCH_RE = re.compile(
    r"<<<SWITCHBOARD_DISPATCH\s*(\{.*?\})\s*>>>",
    re.DOTALL,
)
CONTEST_RE = re.compile(
    r"<<<SWITCHBOARD_CONTEST\s*(\{.*?\})\s*>>>",
    re.DOTALL,
)
VERDICT_RE = re.compile(
    r"<<<SWITCHBOARD_VERDICT\s*(\{.*?\})\s*>>>",
    re.DOTALL,
)
WORK_HINT = re.compile(
    r"\b(delegat\w*|dispatch\w*|implement\w*|continue|improve|team|workers?|agents?|build|fix|ship|do it|go for it|contest|review)\b",
    re.I,
)
DEFAULT_IMPL = ("codex", "hermes", "copilot", "opencode", "agy")


def server_log(msg: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{utcnow()} {msg}"
    print(msg, flush=True)
    try:
        with (DATA_DIR / "server.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def split_dispatches(text: str) -> tuple[str, list[dict]]:
    jobs: list[dict] = []

    def repl(match: re.Match) -> str:
        try:
            obj = json.loads(match.group(1))
        except json.JSONDecodeError:
            return match.group(0)
        worker = str(obj.get("worker") or "").strip().lower()
        prompt = str(obj.get("prompt") or "").strip()
        if worker in WORKERS and prompt:
            jobs.append(
                {
                    "worker": worker,
                    "prompt": prompt,
                    "allow_tools": bool(obj.get("allow_tools", True)),
                    "session_id": str(obj.get("session_id") or "").strip(),
                    "role": str(obj.get("role") or "").strip() or None,
                }
            )
        return ""

    visible = DISPATCH_RE.sub(repl, text or "").strip()
    return visible, jobs


def _extract_json_blocks(text: str, regex: re.Pattern) -> tuple[str, list[dict]]:
    found: list[dict] = []

    def repl(match: re.Match) -> str:
        try:
            found.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            return match.group(0)
        return ""

    return regex.sub(repl, text or "").strip(), found


def split_contests(text: str) -> tuple[str, list[dict]]:
    return _extract_json_blocks(text, CONTEST_RE)


def split_verdicts(text: str) -> tuple[str, list[dict]]:
    return _extract_json_blocks(text, VERDICT_RE)


def norm_dir(path: str) -> str:
    try:
        return str(Path(path).resolve()).lower()
    except Exception:
        return (path or "").lower()


def dir_busy(directory: str) -> bool:
    want = norm_dir(directory)
    root = DATA_DIR / "projects"
    if not root.exists():
        return False
    for p in root.iterdir():
        for card in list_runs(p.name):
            if card.get("status") == "running" and norm_dir(card.get("dir") or "") == want:
                return True
    return False


def project_busy(pid: str) -> bool:
    return any(r.get("status") == "running" for r in list_runs(pid))


def scores_path(pid: str) -> Path:
    return project_dir(pid) / "scores.json"


def load_scores(pid: str) -> dict:
    raw = read_json(scores_path(pid), {})
    out = {}
    for w in WORKERS:
        row = dict(raw.get(w) or {})
        for key in ("runs", "done", "error", "wins", "vetoes"):
            row.setdefault(key, 0)
        out[w] = row
    return out


def bump_score(pid: str, worker: str, field: str, n: int = 1) -> None:
    if worker not in WORKERS:
        return
    scores = load_scores(pid)
    scores[worker][field] = int(scores[worker].get(field, 0)) + n
    write_json(scores_path(pid), scores)


def rank_workers(pid: str) -> list[str]:
    scores = load_scores(pid)

    def key(w: str):
        s = scores[w]
        default = DEFAULT_IMPL.index(w) if w in DEFAULT_IMPL else 99
        return (
            int(s["wins"]) - int(s["vetoes"]),
            int(s["done"]) - int(s["error"]),
            -default,
        )

    return sorted(WORKERS, key=key, reverse=True)


def pick_team(project: dict) -> tuple[list[str], str]:
    ranked = rank_workers(project["id"])
    implementers = ranked[:2]
    rest = [w for w in ranked if w not in implementers]
    reviewer = rest[0] if rest else implementers[-1]
    return implementers, reviewer


def contest_dir(pid: str) -> Path:
    return project_dir(pid) / "contests"


def list_contests(pid: str) -> list:
    root = contest_dir(pid)
    if not root.exists():
        return []
    items = []
    for p in root.glob("*.json"):
        items.append(read_json(p, {}))
    items.sort(key=lambda c: c.get("created_at") or "", reverse=True)
    return items


def load_contest(pid: str, cid: str) -> dict | None:
    path = contest_dir(pid) / f"{cid}.json"
    if not path.exists():
        return None
    return read_json(path, None)


def save_contest(pid: str, contest: dict) -> None:
    write_json(contest_dir(pid) / f"{contest['id']}.json", contest)


def is_git_repo(directory: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", directory, "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception:
        return False
    return proc.returncode == 0 and "true" in (proc.stdout or "").lower()


def create_worktree(project: dict, worker: str, rid: str) -> str:
    dest = project_dir(project["id"]) / "worktrees" / f"{worker}-{rid}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return str(dest.resolve())
    branch = f"sb/{worker}-{rid}"
    try:
        proc = subprocess.run(
            ["git", "-C", project["dir"], "worktree", "add", "-B", branch, str(dest)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except Exception as exc:
        server_log(f"worktree exception: {exc}")
        return project["dir"]
    if proc.returncode != 0:
        server_log(f"worktree failed: {(proc.stderr or proc.stdout or '')[:300]}")
        return project["dir"]
    return str(dest.resolve())


def git_snapshot(directory: str, limit: int = 2000) -> str:
    if not is_git_repo(directory):
        return "(not a git worktree)"
    chunks = []
    for args in (
        ["git", "-C", directory, "status", "-sb"],
        ["git", "-C", directory, "diff", "--stat"],
    ):
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
            chunks.append(proc.stdout or "")
        except Exception as exc:
            chunks.append(str(exc))
    text = clean_text("\n".join(chunks)).strip()
    if len(text) > limit:
        text = text[:limit] + "\n…(truncated)"
    return text or "(no diff)"


def call_grok(prompt: str, cwd: str) -> str:
    refresh_path()
    try:
        proc = subprocess.run(
            [
                "grok",
                "-p",
                prompt,
                "--cwd",
                cwd,
                "--max-turns",
                "1",
                "--verbatim",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except FileNotFoundError:
        return "(grok CLI not on PATH — message stored. Pick a worker to dispatch.)"
    except subprocess.TimeoutExpired:
        return "(grok -p timed out)"
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 and not out:
        err = (proc.stderr or "").strip()
        return f"(grok failed: {err[:400] or proc.returncode})"
    return out or "(empty grok reply)"


def run_grok_desk(project: dict, user_text: str) -> str:
    refresh_path()
    thread = read_jsonl(project_dir(project["id"]) / "thread.jsonl")[-15:]
    runs = list_runs(project["id"])[:12]
    index = []
    for r in runs:
        index.append(
            f"- {r.get('worker')} {r.get('id')} {r.get('status')}: {r.get('summary') or r.get('prompt', '')[:80]}"
        )
    desk = (PROMPTS / "desk.txt").read_text(encoding="utf-8")
    scores = load_scores(project["id"])
    score_lines = [
        f"- {w}: wins={s['wins']} vetoes={s['vetoes']} done={s['done']} error={s['error']}"
        for w, s in scores.items()
    ]
    open_contests = [
        c
        for c in list_contests(project["id"])
        if c.get("status") in ("competing", "reviewing", "awaiting_desk")
    ]
    contest_lines = []
    for c in open_contests[:5]:
        contest_lines.append(
            f"- {c.get('id')} {c.get('status')} goal={c.get('goal', '')[:80]} "
            f"impl={','.join(c.get('implementer_run_ids') or [])} "
            f"review={','.join(c.get('review_run_ids') or [])}"
        )
    lines = [
        desk.strip(),
        "",
        f"Project: {project.get('id')} dir={project.get('dir')}",
        f"Desk note (always in context): {project.get('brief') or '(none)'}",
        "",
        "Scoreboard (higher wins, lower vetoes — pick from this):",
    ]
    lines.extend(score_lines)
    lines.append("")
    lines.append("Open contests:")
    lines.extend(contest_lines or ["(none)"])
    lines.append("")
    lines.append("Recent steering:")
    for ev in thread:
        lines.append(f"{ev.get('role')}: {ev.get('text')}")
    lines.append("")
    lines.append("Run index:")
    lines.extend(index or ["(no runs yet)"])
    lines.append("")
    lines.append("Human:")
    lines.append(user_text)
    prompt = "\n".join(lines)
    out = call_grok(prompt, project["dir"])
    awaiting = [c for c in open_contests if c.get("status") == "awaiting_desk"]
    inflight = [c for c in open_contests if c.get("status") in ("competing", "reviewing")]
    wants = bool(WORK_HINT.search(user_text))
    if awaiting and "<<<SWITCHBOARD_VERDICT" not in out and not out.startswith("("):
        retry = prompt + (
            "\n\nA contest is awaiting your verdict. Emit one "
            "<<<SWITCHBOARD_VERDICT {json} >>> block now (approve one winner or veto)."
        )
        out = call_grok(retry, project["dir"])
    elif (
        wants
        and "<<<SWITCHBOARD_CONTEST" not in out
        and "<<<SWITCHBOARD_DISPATCH" not in out
        and not out.startswith("(")
        and not awaiting
        and not inflight
    ):
        retry = prompt + (
            "\n\nThe human asked for work. Emit one <<<SWITCHBOARD_CONTEST {json} >>> "
            "with two different implementers and a third reviewer, or say you will not."
        )
        out = call_grok(retry, project["dir"])
    if (
        wants
        and "<<<SWITCHBOARD_CONTEST" not in out
        and "<<<SWITCHBOARD_DISPATCH" not in out
        and not awaiting
        and not inflight
    ):
        impl, reviewer = pick_team(project)
        fallback = {
            "goal": user_text,
            "implementers": impl,
            "reviewer": reviewer,
            "allow_tools": True,
        }
        out = (
            (out.rstrip() if out else f"Opening a contest: {', '.join(impl)}, reviewed by {reviewer}.")
            + "\n\n<<<SWITCHBOARD_CONTEST\n"
            + json.dumps(fallback)
            + "\n>>>"
        )
    return out


def run_offload(
    cfg: dict,
    worker: str,
    directory: str,
    prompt: str,
    allow_tools: bool,
    session: str,
    timeout: int = 300,
) -> dict:
    refresh_path()
    script = dispatcher_path(cfg)
    if not script.exists():
        return {
            "worker": worker,
            "session_id": None,
            "exit_code": 2,
            "stdout": "",
            "stderr": f"dispatcher missing: {script}",
            "dir": directory,
            "allow_tools": allow_tools,
        }
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Worker",
        worker,
        "-Dir",
        directory,
        "-Prompt",
        prompt,
    ]
    if allow_tools:
        cmd.append("-AllowTools")
    if session:
        cmd.extend(["-Session", session])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
                timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "worker": worker,
            "session_id": None,
            "exit_code": 124,
            "stdout": "",
            "stderr": "offload timed out",
            "dir": directory,
            "allow_tools": allow_tools,
        }
    raw = (proc.stdout or "").strip()
    parsed = None
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                parsed = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if not parsed:
        parsed = {
            "worker": worker,
            "session_id": None,
            "exit_code": proc.returncode,
            "stdout": raw,
            "stderr": (proc.stderr or "").strip(),
            "dir": directory,
            "allow_tools": allow_tools,
        }
    return parsed


def launch_run_work(cfg: dict, project: dict, card: dict, rdir: Path, session: str) -> None:
    def work() -> None:
        stop = threading.Event()

        def attach() -> None:
            while not stop.is_set():
                if card.get("session_id"):
                    return
                if card.get("worker") == "hermes":
                    sid = discover_hermes_session(card.get("dir") or project["dir"])
                    if sid:
                        card["session_id"] = sid
                        if not card.get("summary") or card.get("summary") == "starting…":
                            card["summary"] = "session attached — worker is running"
                        write_json(rdir / "run.json", card)
                        return
                if stop.wait(1.5):
                    return

        threading.Thread(target=attach, daemon=True).start()
        timeout = 1200 if card.get("allow_tools") else 240
        try:
            result = run_offload(
                cfg,
                card["worker"],
                card.get("dir") or project["dir"],
                card["prompt"],
                bool(card.get("allow_tools")),
                session or card.get("session_id") or "",
                timeout=timeout,
            )
        finally:
            stop.set()
        stdout = result.get("stdout") or ""
        stderr = result.get("stderr") or ""
        work_dir = card.get("dir") or project["dir"]
        status, outcome, code = classify_run(result, work_dir)
        summary = summarize(stdout, stderr)
        if outcome == "wrote_files" and (not summary or summary == "no output"):
            summary = f"wrote files (exit {code})"
        card.update(
            {
                "status": status,
                "outcome": outcome,
                "exit_code": code,
                "session_id": result.get("session_id") or card.get("session_id") or session or None,
                "summary": summary,
                "ended_at": utcnow(),
            }
        )
        write_json(rdir / "run.json", card)
        write_json(rdir / "offload.json", result)
        md = [
            f"# {card['worker']} {card['id']}",
            "",
            f"session: {card.get('session_id')}",
            f"status: {status}",
            f"role: {card.get('role') or ''}",
            "",
            "## stdout",
            "",
            clean_text(stdout) or "(empty)",
            "",
            "## stderr",
            "",
            clean_text(stderr) or "(empty)",
            "",
        ]
        (rdir / "transcript.md").write_text("\n".join(md), encoding="utf-8")
        bump_score(project["id"], card["worker"], "runs")
        bump_score(project["id"], card["worker"], "done" if status == "done" else "error")
        append_jsonl(
            project_dir(project["id"]) / "thread.jsonl",
            {
                "ts": utcnow(),
                "role": "system",
                "text": f"{card['worker']} {card['id']} {status}: {card.get('summary')}",
                "run_ids": [card["id"]],
            },
        )
        pump_queue(cfg, project)
        if card.get("contest_id"):
            advance_contest(cfg, project, card["contest_id"])

    threading.Thread(target=work, daemon=True).start()


def pump_queue(cfg: dict, project: dict) -> None:
    ready = []
    with _lock:
        queued = [r for r in list_runs(project["id"]) if r.get("status") == "queued"]
        queued.sort(key=lambda r: r.get("started_at") or "")
        claimed_dirs = set()
        for nxt in queued:
            work_dir = nxt.get("dir") or project["dir"]
            key = norm_dir(work_dir)
            if key in claimed_dirs or dir_busy(work_dir):
                continue
            rdir = project_dir(project["id"]) / "runs" / nxt["id"]
            nxt["status"] = "running"
            nxt["summary"] = nxt.get("summary") or "starting…"
            write_json(rdir / "run.json", nxt)
            claimed_dirs.add(key)
            ready.append((nxt, rdir))
    for nxt, rdir in ready:
        launch_run_work(cfg, project, nxt, rdir, nxt.get("session_id") or "")


def start_run(
    cfg: dict,
    project: dict,
    worker: str,
    prompt: str,
    allow_tools: bool,
    session: str,
    parent_run_id: str = "",
    role: str = "",
    contest_id: str = "",
    isolate: bool = False,
    work_dir: str = "",
) -> dict:
    rid = uuid.uuid4().hex[:8]
    rdir = project_dir(project["id"]) / "runs" / rid
    rdir.mkdir(parents=True, exist_ok=True)
    directory = work_dir or project["dir"]
    if isolate and is_git_repo(project["dir"]):
        directory = create_worktree(project, worker, rid)
    busy = dir_busy(directory)
    card = {
        "id": rid,
        "project_id": project["id"],
        "worker": worker,
        "session_id": session or None,
        "dir": directory,
        "status": "queued" if busy else "running",
        "summary": "queued behind a running worker" if busy else "starting…",
        "prompt": prompt,
        "role": role or None,
        "contest_id": contest_id or None,
        "parent_run_id": parent_run_id or None,
        "allow_tools": allow_tools,
        "started_at": utcnow(),
        "ended_at": None,
        "transcript_path": "transcript.md",
        "offload_path": "offload.json",
    }
    write_json(rdir / "run.json", card)
    if not busy:
        launch_run_work(cfg, project, card, rdir, session)
    return card


def start_contest(cfg: dict, project: dict, spec: dict, user_text: str) -> tuple[dict, list[dict]]:
    for existing in list_contests(project["id"]):
        if existing.get("status") in ("competing", "reviewing"):
            raise ValueError(
                f"contest {existing.get('id')} is still {existing.get('status')} — wait or veto it first"
            )
    cid = uuid.uuid4().hex[:8]
    goal = str(spec.get("goal") or user_text).strip() or user_text
    impl = []
    for name in spec.get("implementers") or []:
        w = str(name).strip().lower()
        if w in WORKERS and w not in impl:
            impl.append(w)
    if len(impl) < 2:
        picked, _ = pick_team(project)
        for w in picked:
            if w not in impl:
                impl.append(w)
        impl = impl[:2]
    reviewer = str(spec.get("reviewer") or "").strip().lower()
    if reviewer not in WORKERS or reviewer in impl:
        _, reviewer = pick_team(project)
        if reviewer in impl:
            reviewer = next((w for w in rank_workers(project["id"]) if w not in impl), impl[-1])
    isolate = is_git_repo(project["dir"])
    if not isolate:
        impl = impl[:1]
    allow = bool(spec.get("allow_tools", True))
    cards = []
    for worker in impl:
        prompt = (
            f"Contest {cid}. Role: implementer. Produce your best isolated solution.\n"
            f"Do not invoke other harness CLIs. Do not start server.py, serve.ps1, "
            f"serve.cmd, or bind port 8787 — Switchboard is already running.\n"
            f"Goal:\n{goal}"
        )
        cards.append(
            start_run(
                cfg,
                project,
                worker,
                prompt,
                allow,
                "",
                role="implement",
                contest_id=cid,
                isolate=isolate,
            )
        )
    contest = {
        "id": cid,
        "project_id": project["id"],
        "goal": goal,
        "status": "competing",
        "implementers": impl,
        "reviewer": reviewer,
        "implementer_run_ids": [c["id"] for c in cards],
        "review_run_ids": [],
        "winner_run_id": None,
        "reason": None,
        "allow_tools": allow,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    save_contest(project["id"], contest)
    return contest, cards


def _run_status(pid: str, rid: str) -> str:
    card = load_run(pid, rid) or {}
    return card.get("status") or "missing"


def _terminal(status: str) -> bool:
    return status in ("done", "error")


def parse_review_pick(text: str, candidates: list[str]) -> tuple[str | None, str]:
    """Read a reviewer's prose. Never auto-approves — only a suggestion."""
    blob = clean_text(text or "")
    if re.search(r"\bveto[\s-]?all\b", blob, re.I):
        return None, "veto-all"
    m = re.search(r"winner[^.\n]{0,48}?([a-f0-9]{8})", blob, re.I)
    if m and m.group(1) in candidates:
        return m.group(1), "winner-line"
    for rid in candidates:
        if re.search(rf"\bwinner[^\n]{{0,48}}{re.escape(rid)}", blob, re.I):
            return rid, "winner-id"
    return None, "unparsed"


def attach_review_pick(project: dict, contest: dict) -> dict:
    candidates = list(contest.get("implementer_run_ids") or [])
    blob_parts = []
    for rid in contest.get("review_run_ids") or []:
        card = load_run(project["id"], rid) or {}
        blob_parts.append(card.get("summary") or "")
        tpath = (
            project_dir(project["id"])
            / "runs"
            / rid
            / card.get("transcript_path", "transcript.md")
        )
        if tpath.exists():
            blob_parts.append(tpath.read_text(encoding="utf-8", errors="replace"))
    pick, how = parse_review_pick("\n".join(blob_parts), candidates)
    contest["recommended_winner_run_id"] = pick
    contest["recommended_how"] = how
    return contest


def advance_contest(cfg: dict, project: dict, cid: str) -> None:
    contest = load_contest(project["id"], cid)
    if not contest or contest.get("status") in ("approved", "vetoed", "error"):
        return
    impl_states = [_run_status(project["id"], rid) for rid in contest.get("implementer_run_ids") or []]
    if contest["status"] == "competing":
        if not impl_states or not all(_terminal(s) for s in impl_states):
            return
        if not any(s == "done" for s in impl_states):
            contest["status"] = "error"
            contest["reason"] = "every implementer failed"
            contest["updated_at"] = utcnow()
            save_contest(project["id"], contest)
            return
        start_review(cfg, project, contest)
        return
    if contest["status"] == "reviewing":
        rev_states = [_run_status(project["id"], rid) for rid in contest.get("review_run_ids") or []]
        if rev_states and all(_terminal(s) for s in rev_states):
            contest["status"] = "awaiting_desk"
            attach_review_pick(project, contest)
            contest["updated_at"] = utcnow()
            save_contest(project["id"], contest)
            rec = contest.get("recommended_winner_run_id") or "none"
            append_jsonl(
                project_dir(project["id"]) / "thread.jsonl",
                {
                    "ts": utcnow(),
                    "role": "system",
                    "text": (
                        f"Contest {cid} is awaiting an operator verdict. "
                        f"Reviewer suggestion: {rec}. "
                        "It will not approve or apply itself."
                    ),
                    "run_ids": contest.get("review_run_ids") or [],
                },
            )


def start_review(cfg: dict, project: dict, contest: dict) -> None:
    parts = [
        f"Contest {contest['id']} peer review. You do not implement. Compare the candidates.",
        f"Goal: {contest.get('goal')}",
        "Recommend exactly one winner. End with a line: Winner: <8-char-run-id>",
        "Or: Winner: veto-all. Do not approve or apply anything yourself.",
        "Do not spawn other harness CLIs.",
    ]
    for rid in contest.get("implementer_run_ids") or []:
        card = load_run(project["id"], rid) or {}
        parts.append(
            f"\n## candidate {rid} worker={card.get('worker')} status={card.get('status')} dir={card.get('dir')}"
        )
        parts.append(f"summary: {card.get('summary')}")
        parts.append(f"Read the tree on disk at {card.get('dir')}. Stat only:")
        parts.append(git_snapshot(card.get("dir") or project["dir"], limit=1800))
    card = start_run(
        cfg,
        project,
        contest["reviewer"],
        "\n".join(parts),
        False,
        "",
        role="review",
        contest_id=contest["id"],
        isolate=False,
        work_dir=project["dir"],
    )
    contest["review_run_ids"] = [card["id"]]
    contest["status"] = "reviewing"
    contest["updated_at"] = utcnow()
    save_contest(project["id"], contest)


def apply_verdict(project: dict, verdict: dict, source: str = "desk") -> str:
    cid = str(verdict.get("contest_id") or "").strip()
    contest = load_contest(project["id"], cid) if cid else None
    if not contest:
        open_ones = [c for c in list_contests(project["id"]) if c.get("status") == "awaiting_desk"]
        contest = open_ones[0] if open_ones else None
    if not contest:
        return "No contest to judge."
    decision = str(verdict.get("decision") or "").strip().lower()
    reason = str(verdict.get("reason") or "").strip()
    winner = str(verdict.get("winner_run_id") or "").strip()
    if decision == "approve":
        if winner not in (contest.get("implementer_run_ids") or []):
            return f"Winner {winner or '(none)'} is not a candidate of contest {contest['id']}."
        contest["status"] = "approved"
        contest["winner_run_id"] = winner
        contest["reason"] = reason or f"{source} approved {winner}"
        win_card = load_run(project["id"], winner) or {}
        bump_score(project["id"], win_card.get("worker") or "", "wins")
        for rid in contest.get("implementer_run_ids") or []:
            if rid == winner:
                continue
            other = load_run(project["id"], rid) or {}
            bump_score(project["id"], other.get("worker") or "", "vetoes")
        note = (
            f"Desk approved {win_card.get('worker')} {winner} for contest {contest['id']}. "
            f"Tree stays at {win_card.get('dir')} (not merged)."
        )
    elif decision == "veto":
        contest["status"] = "vetoed"
        contest["winner_run_id"] = None
        contest["reason"] = reason or f"{source} vetoed"
        for rid in contest.get("implementer_run_ids") or []:
            other = load_run(project["id"], rid) or {}
            bump_score(project["id"], other.get("worker") or "", "vetoes")
        note = f"Desk vetoed contest {contest['id']}: {contest['reason']}"
    else:
        return f"Unknown decision {decision!r}."
    contest["updated_at"] = utcnow()
    contest["source"] = source
    save_contest(project["id"], contest)
    return note


def git_run(directory: str, args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", directory, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except Exception as exc:
        return 1, "", str(exc)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def git_head(directory: str) -> str | None:
    code, out, _ = git_run(directory, ["rev-parse", "HEAD"])
    return out or None if code == 0 else None


def apply_winner(project: dict, contest: dict) -> tuple[str, dict]:
    if contest.get("status") != "approved":
        raise ValueError("approve a winner before applying")
    if contest.get("applied"):
        raise ValueError("winner is already applied — revert first")
    if dir_busy(project["dir"]):
        raise ValueError("a worker is still writing the project dir")
    winner_id = contest.get("winner_run_id")
    win = load_run(project["id"], winner_id) if winner_id else None
    if not win:
        raise ValueError("winner run is missing")
    proj_dir = project["dir"]
    win_dir = win.get("dir") or ""
    if not win_dir:
        raise ValueError("winner has no worktree")
    if norm_dir(win_dir) == norm_dir(proj_dir):
        contest["applied"] = True
        contest["apply"] = {"noop": True, "applied_at": utcnow()}
        contest["updated_at"] = utcnow()
        save_contest(project["id"], contest)
        return "Winner already lives on the project dir; nothing to copy.", contest
    if not is_git_repo(proj_dir) or not is_git_repo(win_dir):
        raise ValueError("apply needs git on the project and the winner tree")

    pre_head = git_head(proj_dir)
    if not pre_head:
        raise ValueError("could not read project HEAD")
    stash_ref = None
    if git_has_changes(proj_dir):
        code, out, err = git_run(
            proj_dir, ["stash", "push", "-u", "-m", f"sb-pre-apply-{contest['id']}"]
        )
        if code != 0:
            raise ValueError(f"could not stash live tree: {err or out}")
        code, out, err = git_run(proj_dir, ["rev-parse", "refs/stash"])
        stash_ref = out if code == 0 else None

    branch = f"sb/{win.get('worker')}-{win['id']}"
    if git_has_changes(win_dir):
        git_run(win_dir, ["add", "-A"])
        code, out, err = git_run(
            win_dir,
            [
                "-c",
                "user.name=Switchboard",
                "-c",
                "user.email=switchboard@local",
                "commit",
                "-m",
                f"contest {contest['id']} winner {win['id']}",
            ],
        )
        if code != 0 and "nothing to commit" not in f"{out}\n{err}".lower():
            if stash_ref:
                git_run(proj_dir, ["stash", "pop"])
            raise ValueError(f"could not commit winner tree: {err or out}")

    code, out, err = git_run(proj_dir, ["merge", "--no-ff", "--no-edit", branch])
    if code != 0:
        git_run(proj_dir, ["merge", "--abort"])
        if stash_ref:
            git_run(proj_dir, ["stash", "pop"])
        raise ValueError(f"merge conflict applying {branch}: {err or out}")

    stash_restored = False
    stash_note = ""
    if stash_ref:
        code, out, err = git_run(proj_dir, ["stash", "pop"])
        stash_restored = code == 0
        if not stash_restored:
            stash_note = (
                f" Uncommitted work is still stashed (sb-pre-apply-{contest['id']}); "
                "run git stash pop in the project dir."
            )

    code, names, _ = git_run(proj_dir, ["diff", "--name-only", pre_head, "HEAD"])
    changed = [n for n in (names or "").splitlines() if n]
    contest["applied"] = True
    contest["apply"] = {
        "pre_head": pre_head,
        "stash_ref": stash_ref,
        "stash_restored": stash_restored,
        "winner_commit": git_head(win_dir),
        "branch": branch,
        "changed": changed,
        "applied_at": utcnow(),
    }
    contest["updated_at"] = utcnow()
    save_contest(project["id"], contest)
    note = f"Applied {win.get('worker')} {win['id']} onto the project dir ({len(changed)} files)."
    if stash_ref and stash_restored:
        note += " Restored your uncommitted work on top of the merge."
    note += stash_note
    if any(n in ("server.py", "serve.ps1", "serve.cmd") for n in changed):
        note += " Restart Switchboard so the running process matches disk."
    if norm_dir(proj_dir) == norm_dir(str(ROOT)):
        broken = ui_is_broken()
        if broken:
            revert_apply(project, contest)
            raise ValueError(f"apply rolled back — {broken}")
    return note, contest


def ui_is_broken() -> str | None:
    path = ROOT / "ui" / "index.html"
    if not path.exists():
        return "ui/index.html missing"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return str(exc)
    if "<<<<<<<" in text or ">>>>>>>" in text:
        return "merge conflict markers in ui/index.html"
    if "<script>" not in text or "</script>" not in text:
        return "ui/index.html is missing its script"
    return None


def apply_preview(project: dict, contest: dict) -> dict:
    win = load_run(project["id"], contest.get("winner_run_id") or "") if contest.get("winner_run_id") else None
    branch = f"sb/{(win or {}).get('worker')}-{(win or {}).get('id')}" if win else ""
    dirty = git_has_changes(project["dir"])
    behind = 0
    if win and is_git_repo(project["dir"]) and branch:
        code, out, _ = git_run(project["dir"], ["rev-list", "--count", f"{branch}..HEAD"])
        if code == 0 and out.isdigit():
            behind = int(out)
    return {
        "contest_id": contest.get("id"),
        "winner_run_id": contest.get("winner_run_id"),
        "branch": branch,
        "dirty": dirty,
        "commits_ahead_of_winner": behind,
        "applied": bool(contest.get("applied")),
        "self_host": norm_dir(project["dir"]) == norm_dir(str(ROOT)),
    }


def revert_apply(project: dict, contest: dict) -> tuple[str, dict]:
    info = contest.get("apply") or {}
    if not contest.get("applied"):
        raise ValueError("nothing applied to revert")
    if dir_busy(project["dir"]):
        raise ValueError("a worker is still writing the project dir")
    if info.get("noop"):
        contest["applied"] = False
        contest["reverted_at"] = utcnow()
        contest["updated_at"] = utcnow()
        save_contest(project["id"], contest)
        return "Cleared no-op apply.", contest
    pre = info.get("pre_head")
    if not pre:
        raise ValueError("missing pre-apply HEAD")
    code, out, err = git_run(project["dir"], ["reset", "--hard", pre])
    if code != 0:
        raise ValueError(f"reset failed: {err or out}")
    stash_ref = info.get("stash_ref")
    if stash_ref:
        code, out, err = git_run(project["dir"], ["stash", "apply", stash_ref])
        if code != 0:
            raise ValueError(f"reset to {pre[:8]} but could not restore stash: {err or out}")
    contest["applied"] = False
    contest["reverted_at"] = utcnow()
    contest["updated_at"] = utcnow()
    save_contest(project["id"], contest)
    return f"Reverted contest {contest['id']} to {pre[:8]}.", contest


def ask_desk_verdict(cfg: dict, project: dict, contest: dict) -> None:
    reviews = []
    for rid in contest.get("review_run_ids") or []:
        card = load_run(project["id"], rid) or {}
        reviews.append(f"{card.get('worker')} {rid}: {card.get('summary')}")
    packet = (
        f"Contest {contest['id']} needs your verdict.\n"
        f"Goal: {contest.get('goal')}\n"
        f"Candidates: {', '.join(contest.get('implementer_run_ids') or [])}\n"
        f"Reviewer said:\n" + "\n".join(reviews or ["(no review text)"])
        + "\nEmit SWITCHBOARD_VERDICT now."
    )
    raw = run_grok_desk(project, packet)
    visible, verdicts = split_verdicts(raw)
    notes = []
    for verdict in verdicts:
        notes.append(apply_verdict(project, verdict, "desk"))
    text = (visible + ("\n\n" + " ".join(notes) if notes else "")).strip()
    if text:
        append_jsonl(
            project_dir(project["id"]) / "thread.jsonl",
            {"ts": utcnow(), "role": "grok", "text": text, "run_ids": contest.get("review_run_ids") or []},
        )


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    def log_message(self, fmt, *args):
        server_log("%s - %s" % (self.address_string(), fmt % args))

    def _send(self, code: int, body, content_type: str = "application/json") -> None:
        if isinstance(body, (dict, list)):
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            raw = body.encode("utf-8")
        else:
            raw = body
        self.send_response(code)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def _authorized(self) -> bool:
        token = configured_token()
        if not token:
            return True
        got = (self.headers.get("X-Switchboard-Token") or "").strip()
        auth = (self.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            got = auth[7:].strip()
        return got == token

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path.startswith("/api/"):
                return self.api_get(path)
            if path == "/":
                self.path = "/index.html"
            return SimpleHTTPRequestHandler.do_GET(self)
        except Exception as exc:
            sys.stderr.write("GET %s failed: %s\n" % (self.path, exc))
            try:
                self._send(500, {"error": str(exc)})
            except Exception:
                pass

    def do_POST(self):
        try:
            if not self._authorized():
                return self._send(401, {"error": "token required"})
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            if path.startswith("/api/"):
                return self.api_post(path)
            self._send(404, {"error": "not found"})
        except Exception as exc:
            sys.stderr.write("POST %s failed: %s\n" % (self.path, exc))
            try:
                self._send(500, {"error": str(exc)})
            except Exception:
                pass

    def api_get(self, path: str) -> None:
        cfg = load_config()
        if path == "/api/health":
            script = dispatcher_path(cfg)
            grok = which_grok()
            return self._send(
                200,
                {
                    "ok": True,
                    "dispatcher": str(script),
                    "dispatcher_exists": script.exists(),
                    "grok": grok,
                    "host": cfg.get("host") or "127.0.0.1",
                    "port": int(cfg.get("port") or 8787),
                    "pid": os.getpid(),
                    "code_stale": _code_is_stale(),
                    "token_required": bool(configured_token()),
                    "bind": f"{cfg.get('host') or '127.0.0.1'}:{int(cfg.get('port') or 8787)}",
                },
            )
        if path == "/api/workers":
            return self._send(200, {"workers": list(WORKERS)})
        if path == "/api/fs":
            target = (parse_qs(urlparse(self.path).query).get("path") or [""])[0]
            try:
                return self._send(200, list_directory(target))
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            except Exception as exc:
                server_log(f"fs list failed: {exc}")
                return self._send(500, {"error": str(exc)})
        if path == "/api/projects":
            return self._send(200, {"projects": list_projects()})
        m = re.fullmatch(r"/api/projects/([^/]+)", path)
        if m:
            proj = load_project(m.group(1))
            if not proj:
                return self._send(404, {"error": "no project"})
            proj = dict(proj)
            proj["sessions"] = latest_sessions(proj["id"])
            proj["scores"] = load_scores(proj["id"])
            return self._send(200, proj)
        m = re.fullmatch(r"/api/projects/([^/]+)/thread", path)
        if m:
            if not load_project(m.group(1)):
                return self._send(404, {"error": "no project"})
            return self._send(200, {"events": read_jsonl(project_dir(m.group(1)) / "thread.jsonl")})
        m = re.fullmatch(r"/api/projects/([^/]+)/runs", path)
        if m:
            if not load_project(m.group(1)):
                return self._send(404, {"error": "no project"})
            return self._send(200, {"runs": list_runs(m.group(1))})
        m = re.fullmatch(r"/api/projects/([^/]+)/contests", path)
        if m:
            if not load_project(m.group(1)):
                return self._send(404, {"error": "no project"})
            pid = m.group(1)
            proj = load_project(pid)
            contests = list_contests(pid)
            for c in contests:
                if c.get("status") == "awaiting_desk" and "recommended_winner_run_id" not in c:
                    attach_review_pick(proj, c)
                    save_contest(pid, c)
            return self._send(200, {"contests": contests})
        m = re.fullmatch(r"/api/projects/([^/]+)/scores", path)
        if m:
            if not load_project(m.group(1)):
                return self._send(404, {"error": "no project"})
            return self._send(200, {"scores": load_scores(m.group(1)), "ranked": rank_workers(m.group(1))})
        m = re.fullmatch(r"/api/projects/([^/]+)/contests/([^/]+)/apply-preview", path)
        if m:
            if not load_project(m.group(1)):
                return self._send(404, {"error": "no project"})
            contest = load_contest(m.group(1), m.group(2))
            if not contest:
                return self._send(404, {"error": "no contest"})
            proj = load_project(m.group(1))
            return self._send(200, apply_preview(proj, contest))
        m = re.fullmatch(r"/api/runs/([^/]+)", path)
        if m:
            found = find_run(m.group(1))
            if not found:
                return self._send(404, {"error": "no run"})
            return self._send(200, found[1])
        m = re.fullmatch(r"/api/runs/([^/]+)/transcript", path)
        if m:
            found = find_run(m.group(1))
            if not found:
                return self._send(404, {"error": "no run"})
            pid, run = found
            tpath = project_dir(pid) / "runs" / run["id"] / run.get("transcript_path", "transcript.md")
            text = tpath.read_text(encoding="utf-8") if tpath.exists() else "(no transcript yet)"
            return self._send(200, text, "text/plain")
        m = re.fullmatch(r"/api/runs/([^/]+)/diff", path)
        if m:
            found = find_run(m.group(1))
            if not found:
                return self._send(404, {"error": "no run"})
            pid, run = found
            directory = run.get("dir") or project_dir(pid)
            text = git_worktree_diff(str(directory))
            return self._send(200, text, "text/plain")
        self._send(404, {"error": "not found"})

    def api_post(self, path: str) -> None:
        cfg = load_config()
        body = self._read_json()
        if path == "/api/projects":
            directory = body.get("dir") or ""
            if not directory or not Path(directory).is_dir():
                return self._send(400, {"error": "dir must be an existing directory"})
            pid = slugify(body.get("id") or Path(directory).name)
            if load_project(pid):
                return self._send(409, {"error": "project exists"})
            proj = {
                "id": pid,
                "dir": str(Path(directory).resolve()),
                "brief": body.get("brief") or "",
                "created_at": utcnow(),
            }
            write_json(project_dir(pid) / "project.json", proj)
            return self._send(201, proj)
        m = re.fullmatch(r"/api/projects/([^/]+)/message", path)
        if m:
            proj = load_project(m.group(1))
            if not proj:
                return self._send(404, {"error": "no project"})
            text = (body.get("text") or "").strip()
            if not text:
                return self._send(400, {"error": "text required"})
            worker = (body.get("worker") or "").strip().lower()
            allow = bool(body.get("allow_tools"))
            session = (body.get("session_id") or "").strip()
            parent_run_id = (body.get("parent_run_id") or "").strip()
            with _lock:
                append_jsonl(
                    project_dir(proj["id"]) / "thread.jsonl",
                    {"ts": utcnow(), "role": "user", "text": text},
                )
            if worker:
                if worker not in WORKERS:
                    return self._send(400, {"error": "unknown worker"})
                card = start_run(cfg, proj, worker, text, allow, session, parent_run_id)
                if card.get("status") == "queued":
                    note = f"Queued {worker} run {card['id']} (one writer at a time)."
                else:
                    note = f"Dispatched {worker} run {card['id']}."
                append_jsonl(
                    project_dir(proj["id"]) / "thread.jsonl",
                    {"ts": utcnow(), "role": "grok", "text": note, "run_ids": [card["id"]]},
                )
                return self._send(202, {"run": card, "reply": note})
            raw_reply = run_grok_desk(proj, text)
            visible, contests = split_contests(raw_reply)
            visible, verdicts = split_verdicts(visible)
            visible, jobs = split_dispatches(visible)
            run_ids = []
            notes = []
            contest_ids = []
            for verdict in verdicts:
                notes.append(apply_verdict(proj, verdict, "desk"))
            for spec in contests:
                try:
                    contest, cards = start_contest(cfg, proj, spec, text)
                except ValueError as exc:
                    notes.append(str(exc))
                    continue
                contest_ids.append(contest["id"])
                run_ids.extend(c["id"] for c in cards)
                names = ", ".join(contest.get("implementers") or [])
                notes.append(
                    f"Contest {contest['id']}: {names} implement, {contest.get('reviewer')} reviews."
                )
            for job in jobs:
                card = start_run(
                    cfg,
                    proj,
                    job["worker"],
                    job["prompt"],
                    job["allow_tools"],
                    job["session_id"],
                    role=job.get("role") or "",
                )
                run_ids.append(card["id"])
                verb = "Queued" if card.get("status") == "queued" else "Dispatched"
                notes.append(f"{verb} {job['worker']} run {card['id']}.")
            if notes:
                visible = (visible + "\n\n" + " ".join(notes)).strip()
            event = {"ts": utcnow(), "role": "grok", "text": visible}
            if run_ids:
                event["run_ids"] = run_ids
            append_jsonl(project_dir(proj["id"]) / "thread.jsonl", event)
            return self._send(200, {"reply": visible, "runs": run_ids, "contests": contest_ids})
        m = re.fullmatch(r"/api/projects/([^/]+)/contests/([^/]+)/verdict", path)
        if m:
            proj = load_project(m.group(1))
            if not proj:
                return self._send(404, {"error": "no project"})
            body["contest_id"] = m.group(2)
            note = apply_verdict(proj, body, "operator")
            append_jsonl(
                project_dir(proj["id"]) / "thread.jsonl",
                {"ts": utcnow(), "role": "system", "text": note},
            )
            return self._send(200, {"reply": note, "contest": load_contest(proj["id"], m.group(2))})
        m = re.fullmatch(r"/api/projects/([^/]+)/contests/([^/]+)/(apply|revert)", path)
        if m:
            proj = load_project(m.group(1))
            if not proj:
                return self._send(404, {"error": "no project"})
            contest = load_contest(proj["id"], m.group(2))
            if not contest:
                return self._send(404, {"error": "no contest"})
            try:
                if m.group(3) == "apply":
                    note, contest = apply_winner(proj, contest)
                else:
                    note, contest = revert_apply(proj, contest)
            except ValueError as exc:
                return self._send(409, {"error": str(exc)})
            append_jsonl(
                project_dir(proj["id"]) / "thread.jsonl",
                {"ts": utcnow(), "role": "system", "text": note, "run_ids": [contest.get("winner_run_id")] if contest.get("winner_run_id") else []},
            )
            return self._send(200, {"reply": note, "contest": contest})
        self._send(404, {"error": "not found"})

    def do_PATCH(self):
        try:
            if not self._authorized():
                return self._send(401, {"error": "token required"})
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            if not path.startswith("/api/"):
                return self._send(404, {"error": "not found"})
            body = self._read_json()
            m = re.fullmatch(r"/api/projects/([^/]+)", path)
            if not m:
                return self._send(404, {"error": "not found"})
            proj = load_project(m.group(1))
            if not proj:
                return self._send(404, {"error": "no project"})
            if "brief" in body:
                proj["brief"] = str(body.get("brief") or "")
            if "dir" in body:
                directory = body.get("dir") or ""
                if not directory or not Path(directory).is_dir():
                    return self._send(400, {"error": "dir must be an existing directory"})
                proj["dir"] = str(Path(directory).resolve())
            write_json(project_dir(proj["id"]) / "project.json", proj)
            return self._send(200, proj)
        except Exception as exc:
            sys.stderr.write("PATCH %s failed: %s\n" % (self.path, exc))
            try:
                self._send(500, {"error": str(exc)})
            except Exception:
                pass

    def do_DELETE(self):
        try:
            if not self._authorized():
                return self._send(401, {"error": "token required"})
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            if not path.startswith("/api/"):
                return self._send(404, {"error": "not found"})
            m = re.fullmatch(r"/api/projects/([^/]+)", path)
            if not m:
                return self._send(404, {"error": "not found"})
            pid = m.group(1)
            proj = load_project(pid)
            # The persisted ID must match the requested directory. This keeps a
            # malformed request from resolving outside the projects store.
            if not proj or proj.get("id") != pid:
                return self._send(404, {"error": "no project"})
            store = project_dir(pid)
            meta = store / "project.json"
            # Drop the record first so the UI list updates even if rmtree
            # cannot delete locked worktrees on Windows.
            if meta.exists():
                meta.unlink()
            wts = store / "worktrees"
            if wts.exists() and is_git_repo(proj.get("dir") or ""):
                for child in list(wts.iterdir()):
                    if child.is_dir():
                        subprocess.run(
                            ["git", "-C", proj["dir"], "worktree", "remove", "--force", str(child)],
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
            def _onerror(func, name, exc_info):
                try:
                    os.chmod(name, 0o700)
                    func(name)
                except Exception:
                    pass
            with _lock:
                shutil.rmtree(store, onerror=_onerror)
            return self._send(200, {"removed": pid})
        except Exception as exc:
            sys.stderr.write("DELETE %s failed: %s\n" % (self.path, exc))
            try:
                self._send(500, {"error": str(exc)})
            except Exception:
                pass


def recover_orphans() -> None:
    """A reboot leaves cards stuck in running/queued. Mark them so the queue can move."""
    for proj in list_projects():
        pid = proj.get("id")
        if not pid:
            continue
        for card in list_runs(pid):
            if card.get("status") not in ("running", "queued"):
                continue
            rid = card.get("id")
            card["status"] = "error"
            card["ended_at"] = utcnow()
            card["summary"] = (card.get("summary") or "interrupted").split(" (server stopped)")[0] + " (server stopped)"
            rdir = project_dir(pid) / "runs" / rid
            write_json(rdir / "run.json", card)
            server_log(f"recovered orphan {pid}/{rid}")


def reclassify_mixed_runs() -> None:
    """Exit 1 + a dirty worktree is mixed success (AGY often does this)."""
    for proj in list_projects():
        pid = proj.get("id")
        if not pid:
            continue
        for card in list_runs(pid):
            if card.get("status") != "error":
                continue
            directory = card.get("dir") or ""
            if not git_has_changes(directory):
                continue
            rid = card.get("id")
            card["status"] = "done"
            card["outcome"] = "wrote_files"
            card["exit_code"] = card.get("exit_code", 1)
            if not card.get("summary") or card.get("summary") in ("no output", "error"):
                card["summary"] = "wrote files (exit 1)"
            write_json(project_dir(pid) / "runs" / rid / "run.json", card)
            server_log(f"reclassified {pid}/{rid} as wrote_files")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_instance_lock() -> Path:
    path = DATA_DIR / "server.pid"
    if path.exists():
        try:
            old = int(path.read_text(encoding="utf-8").strip())
        except ValueError:
            old = 0
        if old and old != os.getpid() and _pid_alive(old):
            raise SystemExit(f"Switchboard already running as pid {old}")
    return path


def main() -> None:
    refresh_path()
    if "worktrees" in ROOT.parts:
        raise SystemExit(
            f"refusing to start from contest worktree {ROOT}. "
            "Run server.py from the Switchboard repo root."
        )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    recover_orphans()
    reclassify_mixed_runs()
    cfg = load_config()
    host = cfg.get("host") or "127.0.0.1"
    if host not in ("127.0.0.1", "localhost", "::1"):
        server_log(f"warning: binding {host} — prefer 127.0.0.1 until auth is required")
    port = int(cfg.get("port") or 8787)
    acquire_instance_lock()
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if probe.connect_ex((host, port)) == 0:
            raise SystemExit(f"already listening on {host}:{port}")
    finally:
        probe.close()
    # HTTPServer defaults allow_reuse_address=1; on Windows that lets a
    # second process bind 8787 and browsers get empty replies.
    ThreadingHTTPServer.allow_reuse_address = False
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        raise SystemExit(f"could not bind http://{host}:{port}: {exc}") from exc
    pid_path = DATA_DIR / "server.pid"
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    server_log(f"Switchboard http://{host}:{port} pid={os.getpid()}")
    server_log(f"dispatcher {dispatcher_path(cfg)} exists={dispatcher_path(cfg).exists()}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        server_log("bye")
    finally:
        try:
            if pid_path.exists() and pid_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_path.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
