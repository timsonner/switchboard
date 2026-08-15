"""Switchboard local server: static UI + project/thread/run API."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "ui"
DATA_DIR = ROOT / "data"
PROMPTS = ROOT / "prompts"
DEFAULT_SKILLS = (ROOT.parent / "agent-skills").resolve()
WORKERS = ("agy", "opencode", "copilot", "codex", "hermes")

_lock = threading.Lock()


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return s[:64] or "project"


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
    return cfg


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


def exit_status(result: dict) -> str:
    raw = result.get("exit_code", 1)
    try:
        code = int(raw)
    except (TypeError, ValueError):
        code = 1
    return "done" if code == 0 else "error"


def which_grok() -> str | None:
    refresh_path()
    return shutil.which("grok")


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
    lines = [
        desk.strip(),
        "",
        f"Project: {project.get('id')} dir={project.get('dir')}",
        f"Brief: {project.get('brief') or '(none)'}",
        "",
        "Recent steering:",
    ]
    for ev in thread:
        lines.append(f"{ev.get('role')}: {ev.get('text')}")
    lines.append("")
    lines.append("Run index:")
    lines.extend(index or ["(no runs yet)"])
    lines.append("")
    lines.append("Human:")
    lines.append(user_text)
    prompt = "\n".join(lines)
    try:
        proc = subprocess.run(
            [
                "grok",
                "-p",
                prompt,
                "--cwd",
                project["dir"],
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


def run_offload(cfg: dict, worker: str, directory: str, prompt: str, allow_tools: bool, session: str) -> dict:
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
            timeout=300,
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


def start_run(
    cfg: dict,
    project: dict,
    worker: str,
    prompt: str,
    allow_tools: bool,
    session: str,
    parent_run_id: str = "",
) -> dict:
    rid = uuid.uuid4().hex[:8]
    rdir = project_dir(project["id"]) / "runs" / rid
    rdir.mkdir(parents=True, exist_ok=True)
    card = {
        "id": rid,
        "project_id": project["id"],
        "worker": worker,
        "session_id": session or None,
        "dir": project["dir"],
        "status": "running",
        "summary": "",
        "prompt": prompt,
        "parent_run_id": parent_run_id or None,
        "allow_tools": allow_tools,
        "started_at": utcnow(),
        "ended_at": None,
        "transcript_path": "transcript.md",
        "offload_path": "offload.json",
    }
    write_json(rdir / "run.json", card)

    def work():
        result = run_offload(cfg, worker, project["dir"], prompt, allow_tools, session)
        stdout = result.get("stdout") or ""
        stderr = result.get("stderr") or ""
        summary = stdout.strip().splitlines()[0][:200] if stdout.strip() else (stderr[:200] or "no output")
        status = exit_status(result)
        card.update(
            {
                "status": status,
                "session_id": result.get("session_id") or session or None,
                "summary": summary,
                "ended_at": utcnow(),
            }
        )
        write_json(rdir / "run.json", card)
        write_json(rdir / "offload.json", result)
        md = [
            f"# {worker} {rid}",
            "",
            f"session: {card.get('session_id')}",
            f"status: {status}",
            "",
            "## stdout",
            "",
            stdout or "(empty)",
            "",
            "## stderr",
            "",
            stderr or "(empty)",
            "",
        ]
        (rdir / "transcript.md").write_text("\n".join(md), encoding="utf-8")

    threading.Thread(target=work, daemon=True).start()
    return card


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI_DIR), **kwargs)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

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

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

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
                },
            )
        if path == "/api/workers":
            return self._send(200, {"workers": list(WORKERS)})
        if path == "/api/projects":
            return self._send(200, {"projects": list_projects()})
        m = re.fullmatch(r"/api/projects/([^/]+)", path)
        if m:
            proj = load_project(m.group(1))
            if not proj:
                return self._send(404, {"error": "no project"})
            proj = dict(proj)
            proj["sessions"] = latest_sessions(proj["id"])
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
                note = f"Dispatched {worker} run {card['id']}."
                append_jsonl(
                    project_dir(proj["id"]) / "thread.jsonl",
                    {"ts": utcnow(), "role": "grok", "text": note, "run_ids": [card["id"]]},
                )
                return self._send(202, {"run": card, "reply": note})
            reply = run_grok_desk(proj, text)
            append_jsonl(
                project_dir(proj["id"]) / "thread.jsonl",
                {"ts": utcnow(), "role": "grok", "text": reply},
            )
            return self._send(200, {"reply": reply})
        self._send(404, {"error": "not found"})

    def do_PATCH(self):
        try:
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


def main() -> None:
    refresh_path()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    host = cfg.get("host") or "127.0.0.1"
    port = int(cfg.get("port") or 8787)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Switchboard http://{host}:{port}", flush=True)
    print(f"dispatcher {dispatcher_path(cfg)} exists={dispatcher_path(cfg).exists()}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye", flush=True)


if __name__ == "__main__":
    main()
