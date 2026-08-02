"""OT Range control panel — a local web GUI over the same make targets
and scripts the README documents.

Not a new attack surface: every action here shells out to a command
already in this repo (`make up`, `scenarios/run_scenario.sh`, etc.) with
a fixed, hardcoded argument list — nothing here reads from the network
or takes untrusted input. Binds to loopback only, on purpose: this
process has the power to start/stop containers, never expose it beyond
127.0.0.1.

Usage: make panel   (or: .venv/bin/python -m panel.app)
"""

from __future__ import annotations

import json
import logging
import subprocess  # nosec B404 -- literal, hardcoded argument lists only; see call sites below
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request

from scenarios.catalog import SCENARIOS, SCENARIOS_BY_ID
from scenarios.flags import FLAGS_BY_SCENARIO
from scenarios.flags import check as check_flag
from tools.status import docker_container_status, http_ok, tcp_open

LOG = logging.getLogger("ot_range.panel")

REPO_ROOT = Path(__file__).resolve().parent.parent
PANEL_PORT = 8099

app = Flask(__name__)

DOC_FILES = {
    "briefing": "briefing.md",
    "answer-key": "answer-key.md",
    "detection": "detection.md",
    "expected-impact": "expected-impact.md",
}

STACK_COMMANDS = {
    "up": ["make", "up"],
    "down": ["make", "down"],
    "reset": ["make", "reset"],
}

DOCKER_CONTAINERS = [
    "ot-range-process-sim-1",
    "ot-range-openplc-1",
    "ot-range-hmi-1",
    "ot-range-historian-1",
    "ot-range-postgres-1",
    "ot-range-grafana-1",
    "ot-range-router-1",
]


# --- background job execution (drives the live output panel) ---


@dataclass
class Job:
    id: str
    command: list[str]
    lines: list[str] = field(default_factory=list)
    done: bool = False
    returncode: int | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


_jobs: dict[str, Job] = {}
_jobs_guard = threading.Lock()
_current_job_id: str | None = None


def _run_job(job: Job) -> None:
    proc = subprocess.Popen(  # nosec B603
        job.command,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if proc.stdout is None:
        raise RuntimeError("subprocess.Popen was not given stdout=PIPE")
    for raw_line in proc.stdout:
        with job.lock:
            job.lines.append(raw_line.rstrip("\n"))
    proc.wait()
    with job.lock:
        job.done = True
        job.returncode = proc.returncode


def start_job(command: list[str]) -> str | None:
    """Returns a job id, or None if another job is already running."""
    global _current_job_id
    with _jobs_guard:
        if _current_job_id is not None and not _jobs[_current_job_id].done:
            return None
        job_id = uuid.uuid4().hex[:10]
        job = Job(id=job_id, command=command)
        _jobs[job_id] = job
        _current_job_id = job_id
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return job_id


# --- status ---


def collect_status() -> dict:
    containers = []
    any_present = False
    all_healthy = True
    for name in DOCKER_CONTAINERS:
        raw = docker_container_status(name)
        if raw is None:
            containers.append({"name": name, "state": "absent", "health": "", "ok": False})
            all_healthy = False
            continue
        any_present = True
        state, _, health = raw.partition("|")
        ok = state == "running" and health in ("", "healthy")
        all_healthy = all_healthy and ok
        containers.append({"name": name, "state": state, "health": health, "ok": ok})

    ports = {
        "modbus_openplc": tcp_open("127.0.0.1", 502),
        "modbus_sim": tcp_open("127.0.0.1", 5502),
        "openplc_web": http_ok("http://127.0.0.1:8080"),
        "hmi": http_ok("http://127.0.0.1:8090"),
        "grafana": http_ok("http://127.0.0.1:3000"),
    }
    loopback = {
        "process_sim": tcp_open("127.0.0.1", 5502),
        "tap": tcp_open("127.0.0.1", 5020),
    }
    return {
        "docker": {
            "containers": containers,
            "ports": ports,
            "any_present": any_present,
            "all_healthy": all_healthy and any_present,
        },
        "loopback": loopback,
        "busy": _current_job_id is not None and not _jobs[_current_job_id].done
        if _current_job_id
        else False,
    }


# --- routes ---


# Per-scenario accent color: not arbitrary decoration — sky for recon
# (informational), orange for a destructive-but-caught command, gold
# for the flagship, red for S06's deliberately undetected gap.
SCENARIO_COLORS = {
    "S01": "#38bdf8",
    "S03": "#fb923c",
    "S05": "#fbbf24",
    "S06": "#f87171",
}


@app.route("/")
def index():
    scenarios = [
        {
            "id": s.id,
            "title": s.title,
            "hook": s.hook,
            "impact": s.impact,
            "caught_by": s.caught_by,
            "color": SCENARIO_COLORS.get(s.id, "#22d3ee"),
            "modes": [
                {"label": m.label, "requires_docker": m.requires_docker_stack} for m in s.modes
            ],
        }
        for s in SCENARIOS
    ]
    return render_template("index.html", scenarios=scenarios)


@app.route("/api/status")
def api_status():
    return jsonify(collect_status())


@app.route("/api/stack/<action>", methods=["POST"])
def api_stack(action: str):
    command = STACK_COMMANDS.get(action)
    if command is None:
        abort(404)
    job_id = start_job(command)
    if job_id is None:
        return jsonify(error="busy"), 409
    return jsonify(job_id=job_id)


@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.get_json(silent=True) or {}
    scenario = SCENARIOS_BY_ID.get(data.get("scenario", ""))
    if scenario is None:
        return jsonify(error="unknown scenario"), 400
    try:
        mode_index = int(data.get("mode_index", 0))
        mode = scenario.modes[mode_index]
    except (ValueError, IndexError):
        return jsonify(error="unknown mode"), 400
    job_id = start_job(mode.command)
    if job_id is None:
        return jsonify(error="busy"), 409
    return jsonify(job_id=job_id)


@app.route("/api/stream/<job_id>")
def api_stream(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        abort(404)

    def generate():
        idx = 0
        while True:
            with job.lock:
                new_lines = job.lines[idx:]
                idx = len(job.lines)
                done, returncode = job.done, job.returncode
            for text_line in new_lines:
                yield f"data: {json.dumps(text_line)}\n\n"
            if done:
                yield f"event: done\ndata: {returncode}\n\n"
                return
            time.sleep(0.3)

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/docs/<scenario_id>/<doc>")
def api_docs(scenario_id: str, doc: str):
    scenario = SCENARIOS_BY_ID.get(scenario_id)
    filename = DOC_FILES.get(doc)
    if scenario is None or filename is None:
        abort(404)
    path = scenario.dirname / filename
    if not path.is_file():
        abort(404)
    return Response(path.read_text(), mimetype="text/plain")


@app.route("/api/flags")
def api_flags():
    # Prompts and hints only — never the accepted answers. Checked
    # server-side in api_flags_check so answers never sit in page
    # source; that's the whole point of routing this through an API
    # instead of just rendering FLAGS_BY_SCENARIO into the template.
    return jsonify(
        {
            scenario_id: [{"id": f.id, "prompt": f.prompt, "hint": f.hint} for f in flags]
            for scenario_id, flags in FLAGS_BY_SCENARIO.items()
        }
    )


@app.route("/api/flags/check", methods=["POST"])
def api_flags_check():
    data = request.get_json(silent=True) or {}
    scenario_id = data.get("scenario", "")
    flag_id = data.get("flag_id", "")
    answer = data.get("answer", "")
    flags = FLAGS_BY_SCENARIO.get(scenario_id, [])
    flag = next((f for f in flags if f.id == flag_id), None)
    if flag is None:
        return jsonify(error="unknown flag"), 404
    return jsonify(correct=check_flag(flag, answer))


def _is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def _open_browser(url: str) -> None:
    time.sleep(1.0)
    try:
        if _is_wsl():
            # No native GUI browser inside WSL — hand off to Windows.
            # "start" treats its first quoted arg as a window title, so
            # the empty string placeholder is required, not a typo.
            subprocess.run(["cmd.exe", "/c", "start", "", url], check=False)  # nosec B603 B607
        else:
            webbrowser.open(url)
    except OSError as exc:
        LOG.warning("could not auto-open a browser (%s) — open %s yourself", exc, url)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    url = f"http://127.0.0.1:{PANEL_PORT}"
    print(f"\nOT Range control panel: {url}\n(Ctrl+C to stop)\n")
    threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    # 127.0.0.1 only, deliberately — this process can start/stop the
    # stack and run attack scripts; never expose it beyond loopback.
    app.run(host="127.0.0.1", port=PANEL_PORT, debug=False)


if __name__ == "__main__":
    main()
