"""OT Range control panel — a local web GUI over the same make targets
and scripts the README documents.

Every execution action shells out to a command already in this repo
(`make up`, `scenarios/run_scenario.sh`, etc.) with a fixed, hardcoded
argument list. Profile, policy, and training inputs are validated and
persisted in a local SQLite database. The panel binds to loopback only,
on purpose: this process can start/stop containers and run attack
scenarios, so never expose it beyond 127.0.0.1.

Usage: make panel   (or: .venv/bin/python -m panel.app)
"""

from __future__ import annotations

import json
import logging
import secrets
import subprocess  # nosec B404 -- literal, hardcoded argument lists only; see call sites below
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    current_app,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from panel.auth import (
    AuthenticationError,
    AuthenticationThrottled,
    InstructorAuth,
    PasswordValidationError,
)
from panel.storage import Storage, StorageConflict, utc_now
from panel.topology import get_topology
from panel.training_service import (
    SOLUTION_DOCS,
    TrainingError,
    TrainingNotFound,
    TrainingService,
)
from scenarios.catalog import LEARNING_OBJECTIVES, SCENARIOS, SCENARIOS_BY_ID
from tools.status import docker_container_status, http_ok, tcp_open

LOG = logging.getLogger("ot_range.panel")

REPO_ROOT = Path(__file__).resolve().parent.parent
PANEL_PORT = 8099

app = Flask(__name__, instance_relative_config=True)
app.config.update(
    DATABASE_PATH=str(Path(app.instance_path) / "ot-range.db"),
    INSTRUCTOR_COOKIE_NAME="ot_range_instructor",
    STUDENT_COOKIE_NAME="ot_range_student",
    COOKIE_SECURE=False,
    INSTRUCTOR_IDLE_MINUTES=30,
    INSTRUCTOR_ABSOLUTE_HOURS=8,
)

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


# --- persistence / local session helpers ---


def get_storage() -> Storage:
    path = Path(current_app.config["DATABASE_PATH"])
    storage = current_app.extensions.get("ot_range_storage")
    if storage is None or storage.path != path:
        storage = Storage(path)
        storage.initialize([scenario.id for scenario in SCENARIOS])
        current_app.extensions["ot_range_storage"] = storage
        current_app.extensions.pop("ot_range_auth", None)
    return storage


def get_auth() -> InstructorAuth:
    auth = current_app.extensions.get("ot_range_auth")
    storage = get_storage()
    if auth is None or auth.storage.path != storage.path:
        auth = InstructorAuth(
            storage,
            idle_timeout=timedelta(minutes=current_app.config["INSTRUCTOR_IDLE_MINUTES"]),
            absolute_timeout=timedelta(hours=current_app.config["INSTRUCTOR_ABSOLUTE_HOURS"]),
            scrypt_n=current_app.config.get("SCRYPT_N", 2**14),
            throttle_base_seconds=current_app.config.get("AUTH_THROTTLE_BASE_SECONDS", 1),
        )
        current_app.extensions["ot_range_auth"] = auth
    return auth


def get_training_service() -> TrainingService:
    return TrainingService(get_storage())


def _cookie_secure() -> bool:
    return bool(current_app.config["COOKIE_SECURE"] or request.is_secure)


def _set_instructor_cookie(response, token: str):
    response.set_cookie(
        current_app.config["INSTRUCTOR_COOKIE_NAME"],
        token,
        httponly=True,
        secure=_cookie_secure(),
        samesite="Strict",
        path="/",
        max_age=int(current_app.config["INSTRUCTOR_ABSOLUTE_HOURS"] * 3600),
    )
    return response


def _clear_instructor_cookie(response):
    response.delete_cookie(
        current_app.config["INSTRUCTOR_COOKIE_NAME"],
        httponly=True,
        secure=_cookie_secure(),
        samesite="Strict",
        path="/",
    )
    return response


def _set_student_cookie(response, token: str):
    response.set_cookie(
        current_app.config["STUDENT_COOKIE_NAME"],
        token,
        httponly=True,
        secure=_cookie_secure(),
        samesite="Strict",
        path="/",
        max_age=60 * 60 * 24 * 365,
    )
    return response


def _instructor_token() -> str | None:
    return request.cookies.get(current_app.config["INSTRUCTOR_COOKIE_NAME"])


def instructor_authenticated() -> bool:
    return get_auth().validate_session(_instructor_token()).authenticated


def require_instructor(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not instructor_authenticated():
            if request.path.startswith("/api/"):
                return jsonify(error="instructor authorization required"), 401
            return redirect(url_for("instructor_login_page"))
        g.instructor_authenticated = True
        return view(*args, **kwargs)

    return wrapped


def active_profile_id(*, required: bool = True) -> str | None:
    token = request.cookies.get(current_app.config["STUDENT_COOKIE_NAME"])
    session = get_storage().get_student_session(token) if token else None
    if session:
        return session["profile_id"]
    if required:
        raise TrainingError("Select a local learner profile first.")
    return None


def require_active_profile(profile_id: str) -> str:
    active = active_profile_id()
    if active != profile_id:
        raise TrainingError("That learner profile is not active in this workspace.")
    return active


def request_json_object() -> dict:
    data = request.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise TrainingError("Request body must be a JSON object.")
    return data


def _profile_fields(data: dict) -> dict[str, str | None]:
    allowed = (
        "display_name",
        "learner_id",
        "organization",
        "course",
        "section",
        "instructor_name",
    )
    result: dict[str, str | None] = {}
    for key in allowed:
        value = data.get(key)
        if value is None:
            result[key] = None
            continue
        if not isinstance(value, str):
            raise TrainingError(f"{key} must be text")
        cleaned = value.strip()
        if len(cleaned) > (120 if key == "display_name" else 200):
            raise TrainingError(f"{key} is too long")
        result[key] = cleaned or None
    if not result.get("display_name"):
        raise TrainingError("Display name is required.")
    return result


def scenario_payload() -> list[dict]:
    return [
        {
            "id": scenario.id,
            "title": scenario.title,
            "hook": scenario.hook,
            "impact": scenario.impact,
            "caught_by": scenario.caught_by,
            "severity": scenario.severity,
            "objectives": [LEARNING_OBJECTIVES[oid] for oid in scenario.objectives],
            "modes": [
                {
                    "label": mode.label,
                    "description": mode.description,
                    "requires_docker": mode.requires_docker_stack,
                    "technical_command": " ".join(mode.command),
                }
                for mode in scenario.modes
            ],
            "difficulty": scenario.difficulty,
            "estimated_duration": scenario.estimated_duration,
            "prerequisites": scenario.prerequisites,
            "primary_skills": scenario.primary_skills,
            "evidence_sources": scenario.evidence_sources,
            "recommended_training_mode": scenario.recommended_training_mode,
            "process_impact_rating": scenario.process_impact_rating,
            "detection_coverage_state": scenario.detection_coverage_state,
        }
        for scenario in SCENARIOS
    ]


@app.errorhandler(TrainingError)
def handle_training_error(exc: TrainingError):
    return jsonify(error=str(exc)), exc.status_code


# --- routes ---


@app.route("/")
def index():
    return render_template("chooser.html")


@app.route("/student")
def student_lab():
    profile_id = active_profile_id(required=False)
    if not profile_id:
        return redirect(url_for("index"))
    profile = get_storage().get_profile(profile_id)
    if profile is None:
        return redirect(url_for("index"))
    return render_template(
        "index.html",
        scenarios=scenario_payload(),
        solution_docs=sorted(SOLUTION_DOCS),
        profile=profile,
    )


@app.route("/instructor/login")
def instructor_login_page():
    if instructor_authenticated():
        return redirect(url_for("instructor_console"))
    return render_template("instructor_login.html")


@app.route("/instructor")
@require_instructor
def instructor_console():
    return render_template("instructor.html", scenarios=scenario_payload())


@app.route("/api/instructor/status")
def api_instructor_status():
    auth = get_auth()
    return jsonify(
        configured=auth.configured,
        authenticated=auth.validate_session(_instructor_token()).authenticated,
    )


@app.route("/api/instructor/setup", methods=["POST"])
def api_instructor_setup():
    password = request_json_object().get("password")
    try:
        token = get_auth().setup(password)
    except PasswordValidationError as exc:
        return jsonify(error=str(exc)), 400
    except AuthenticationError:
        return jsonify(error="Instructor setup is unavailable."), 409
    return _set_instructor_cookie(jsonify(configured=True, authenticated=True), token), 201


@app.route("/api/instructor/login", methods=["POST"])
def api_instructor_login():
    password = request_json_object().get("password")
    try:
        token = get_auth().login(password)
    except AuthenticationThrottled as exc:
        response = jsonify(error="Incorrect credentials or login temporarily unavailable.")
        response.headers["Retry-After"] = str(exc.retry_after)
        return response, 429
    except AuthenticationError:
        return jsonify(error="Incorrect credentials or login temporarily unavailable."), 401
    return _set_instructor_cookie(jsonify(authenticated=True), token)


@app.route("/api/instructor/logout", methods=["POST"])
def api_instructor_logout():
    get_auth().logout(_instructor_token())
    return _clear_instructor_cookie(jsonify(authenticated=False))


@app.route("/api/instructor/change-password", methods=["POST"])
@require_instructor
def api_instructor_change_password():
    data = request_json_object()
    try:
        get_auth().change_password(
            _instructor_token(), data.get("current_password"), data.get("new_password")
        )
    except PasswordValidationError as exc:
        return jsonify(error=str(exc)), 400
    except AuthenticationError:
        return jsonify(error="Incorrect credentials."), 401
    return _clear_instructor_cookie(jsonify(authenticated=False, sessions_invalidated=True))


@app.route("/api/instructor/sessions/invalidate", methods=["POST"])
@require_instructor
def api_instructor_invalidate_sessions():
    password = request_json_object().get("password")
    try:
        count = get_auth().invalidate_all(_instructor_token(), password)
    except AuthenticationError:
        return jsonify(error="Incorrect credentials."), 401
    return _clear_instructor_cookie(jsonify(invalidated=count, authenticated=False))


@app.route("/api/profiles", methods=["GET", "POST"])
def api_profiles():
    storage = get_storage()
    if request.method == "GET":
        return jsonify(
            profiles=storage.list_profiles(),
            activeProfileId=active_profile_id(required=False),
        )
    fields = _profile_fields(request_json_object())
    profile = storage.create_profile(uuid.uuid4().hex, fields)
    token = secrets.token_urlsafe(32)
    storage.create_student_session(token, profile["id"])
    return _set_student_cookie(jsonify(profile=profile), token), 201


@app.route("/api/profiles/<profile_id>/select", methods=["POST"])
def api_profile_select(profile_id: str):
    storage = get_storage()
    profile = storage.get_profile(profile_id)
    if profile is None:
        raise TrainingNotFound("unknown profile")
    old_token = request.cookies.get(current_app.config["STUDENT_COOKIE_NAME"])
    if old_token:
        storage.delete_student_session(old_token)
    token = secrets.token_urlsafe(32)
    storage.create_student_session(token, profile_id)
    return _set_student_cookie(jsonify(profile=profile), token)


@app.route("/api/profiles/<profile_id>", methods=["GET", "PATCH", "DELETE"])
def api_profile(profile_id: str):
    require_active_profile(profile_id)
    storage = get_storage()
    profile = storage.get_profile(profile_id)
    if profile is None:
        raise TrainingNotFound("unknown profile")
    if request.method == "GET":
        return jsonify(profile=profile, training=get_training_service().all_state(profile_id))
    if request.method == "PATCH":
        patch = request_json_object()
        merged = {key: patch.get(key, profile.get(key)) for key in _profile_fields(profile)}
        updated = storage.update_profile(profile_id, _profile_fields(merged))
        return jsonify(profile=updated)
    confirmation = request_json_object().get("confirm_display_name")
    if confirmation != profile["display_name"]:
        return jsonify(error="Enter the learner display name to confirm deletion."), 400
    storage.delete_profile(profile_id)
    response = jsonify(deleted=True)
    response.delete_cookie(
        current_app.config["STUDENT_COOKIE_NAME"],
        httponly=True,
        secure=_cookie_secure(),
        samesite="Strict",
        path="/",
    )
    return response


@app.route("/api/profiles/<profile_id>/export")
def api_profile_export(profile_id: str):
    require_active_profile(profile_id)
    return jsonify(get_training_service().profile_report(profile_id))


@app.route("/api/profiles/<profile_id>/progress/<scenario_id>/reset", methods=["POST"])
def api_profile_attempt_reset(profile_id: str, scenario_id: str):
    require_active_profile(profile_id)
    get_training_service().reset_attempt(profile_id, scenario_id)
    return jsonify(state=get_training_service().state(profile_id, scenario_id))


@app.route("/api/profiles/<profile_id>/reset-all", methods=["POST"])
def api_profile_reset_all(profile_id: str):
    require_active_profile(profile_id)
    get_training_service().reset_all(profile_id)
    return jsonify(training=get_training_service().all_state(profile_id))


@app.route("/api/training")
def api_training():
    profile_id = active_profile_id()
    return jsonify(get_training_service().all_state(profile_id))


@app.route("/api/training/<scenario_id>", methods=["GET", "PATCH"])
def api_training_scenario(scenario_id: str):
    profile_id = active_profile_id()
    service = get_training_service()
    if request.method == "GET":
        return jsonify(state=service.state(profile_id, scenario_id))
    data = request_json_object()
    if "notes" in data:
        state = service.set_notes(profile_id, scenario_id, data["notes"])
    else:
        start = data.get("start", False)
        if type(start) is not bool:
            raise TrainingError("start must be true or false")
        state = service.configure_attempt(
            profile_id,
            scenario_id,
            mode=data.get("mode"),
            start=start,
        )
    return jsonify(state=state)


@app.route("/api/policies")
def api_policies():
    return jsonify(get_training_service().public_policies())


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
    data = request_json_object()
    scenario_id = data.get("scenario", "")
    scenario = SCENARIOS_BY_ID.get(scenario_id) if isinstance(scenario_id, str) else None
    if scenario is None:
        return jsonify(error="unknown scenario"), 400
    try:
        mode_index = int(data.get("mode_index", 0))
        if mode_index < 0:
            raise IndexError
        mode = scenario.modes[mode_index]
    except (TypeError, ValueError, IndexError):
        return jsonify(error="unknown mode"), 400
    profile_id = active_profile_id()
    service = get_training_service()
    service.require_scenario(scenario.id)
    training_mode = service.validate_mode(data.get("training_mode"))
    job_id = start_job(mode.command)
    if job_id is None:
        return jsonify(error="busy"), 409
    service.configure_attempt(profile_id, scenario.id, mode=training_mode, start=True)
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
    profile_id = active_profile_id()
    get_training_service().open_document(profile_id, scenario_id, doc)
    return Response(path.read_text(), mimetype="text/plain")


@app.route("/api/instructor/docs/<scenario_id>/<doc>")
@require_instructor
def api_instructor_docs(scenario_id: str, doc: str):
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
    # Prompts, points, and hint *costs* only — never hint text, never
    # accepted answers. Checked server-side in api_flags_check so
    # answers never sit in page source; hint text is only ever sent
    # by api_flags_hint, one level at a time, when a student actually
    # reveals it.
    active_profile_id()
    return jsonify(get_training_service().flag_payload())


@app.route("/api/flags/<scenario_id>/<flag_id>/hint/<int:level>")
def api_flags_hint(scenario_id: str, flag_id: str, level: int):
    profile_id = active_profile_id()
    return jsonify(get_training_service().reveal_hint(profile_id, scenario_id, flag_id, level))


@app.route("/api/topology")
def api_topology():
    return jsonify(get_topology())


@app.route("/api/flags/check", methods=["POST"])
def api_flags_check():
    data = request_json_object()
    scenario_id = data.get("scenario", "")
    flag_id = data.get("flag_id", "")
    answer = data.get("answer", "")
    profile_id = active_profile_id()
    result = get_training_service().submit_flag(
        profile_id,
        scenario_id,
        flag_id,
        answer,
        mode=data.get("training_mode"),
    )
    # Preserve the original public response contract. Authoritative
    # state is fetched from /api/training after the mutation.
    return jsonify(correct=result["correct"])


@app.route("/api/instructor/overview")
@require_instructor
def api_instructor_overview():
    return jsonify(analytics=get_storage().analytics(), status=collect_status())


@app.route("/api/instructor/profiles")
@require_instructor
def api_instructor_profiles():
    return jsonify(profiles=get_storage().list_profiles())


@app.route("/api/instructor/profiles/<profile_id>/export")
@require_instructor
def api_instructor_profile_export(profile_id: str):
    return jsonify(get_training_service().profile_report(profile_id))


@app.route("/api/instructor/profiles/export-all")
@require_instructor
def api_instructor_export_all():
    service = get_training_service()
    reports = [service.profile_report(profile["id"]) for profile in get_storage().list_profiles()]
    return jsonify(
        generatedAt=utc_now(),
        localData=True,
        profiles=reports,
    )


def _reauthenticate_instructor(data: dict) -> bool:
    return get_auth().verify_password(data.get("password"))


@app.route("/api/instructor/profiles/<profile_id>", methods=["DELETE"])
@require_instructor
def api_instructor_profile_delete(profile_id: str):
    data = request_json_object()
    if not _reauthenticate_instructor(data):
        return jsonify(error="Incorrect credentials."), 401
    if not get_storage().delete_profile(profile_id):
        raise TrainingNotFound("unknown profile")
    get_storage().record_security_event("profile_deleted_by_instructor", {"profile_id": profile_id})
    return jsonify(deleted=True)


@app.route("/api/instructor/profiles/<profile_id>/reset", methods=["POST"])
@require_instructor
def api_instructor_profile_reset(profile_id: str):
    data = request_json_object()
    if not _reauthenticate_instructor(data):
        return jsonify(error="Incorrect credentials."), 401
    if get_storage().get_profile(profile_id) is None:
        raise TrainingNotFound("unknown profile")
    get_training_service().reset_all(profile_id)
    get_storage().record_security_event(
        "profile_progress_reset_by_instructor", {"profile_id": profile_id}
    )
    return jsonify(reset=True)


@app.route("/api/instructor/policies", methods=["GET", "PUT"])
@require_instructor
def api_instructor_policies():
    storage = get_storage()
    if request.method == "GET":
        return jsonify(storage.get_policies())
    data = request_json_object()
    policy_keys = {
        "scoredModeEnabled": "scored_mode_enabled",
        "independentModeEnabled": "independent_mode_enabled",
        "guidedModeEnabled": "guided_mode_enabled",
        "hintsEnabled": "hints_enabled",
        "answerKeyEnabled": "answer_key_enabled",
        "walkthroughEnabled": "walkthrough_enabled",
    }
    patch = {}
    for external, internal in policy_keys.items():
        if external not in data:
            continue
        if type(data[external]) is not bool:
            raise TrainingError(f"{external} must be true or false")
        patch[internal] = data[external]
    availability = data.get("scenarioAvailability")
    if availability is not None:
        if not isinstance(availability, dict):
            raise TrainingError("scenarioAvailability must be an object")
        unknown = set(availability) - set(SCENARIOS_BY_ID)
        if unknown:
            raise TrainingError("scenarioAvailability contains an unknown scenario")
        if any(type(enabled) is not bool for enabled in availability.values()):
            raise TrainingError("scenario availability values must be true or false")
    try:
        policies = storage.update_policies(patch, availability)
    except StorageConflict as exc:
        return jsonify(error=str(exc)), 400
    storage.record_security_event("instructor_policies_updated")
    return jsonify(policies)


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
