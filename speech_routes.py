"""Explicit lifecycle and authenticated proxy for CE's local speech process."""

import atexit
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from collections import deque

import requests
from flask import Blueprint, current_app, jsonify, request, session
from gevent import get_hub
from gevent.monkey import get_original

import model_routes
from app_settings import get_setting, set_setting
from auth import login_required
from runtime_config import get_llama_main_port, get_llama_title_port


speech_routes = Blueprint("speech_routes", __name__)
MAX_AUDIO_BYTES = 16000 * 2 * 120 + 44
_lock = threading.RLock()
_transcription_lock = threading.Lock()
_process = None
_state = {"status": "stopped", "model": "", "device": "", "message": "Speech-to-text model is not running."}
_port = None
_token = ""
_logs = []
_console_pending = deque(maxlen=500)
_console_started = False
_native_start_thread = get_original("_thread", "start_new_thread")
_native_sleep = get_original("time", "sleep")
_native_popen = get_original("subprocess", "Popen")


def _safe_log_line(message, token=""):
    line = str(message)
    if token:
        line = line.replace(token, "[redacted]")
    line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line)
    if re.search(r"(?i)(?:ce_speech_token|authorization|(?:set-)?cookie|password|secret|api[_-]?key|(?:hf|access|refresh)[_-]?token|session(?:_id)?)[\"']?\s*[:=]|\bbearer\s+\S+", line):
        return "[sensitive log line redacted]"
    return re.sub(r"[\x00-\x1f\x7f]", " ", line)[:4096]


def _write_console():
    # Console I/O must not hold up the sole child-pipe reader or gevent's hub.
    while True:
        try:
            line = _console_pending.popleft()
        except IndexError:
            _native_sleep(0.1)
            continue
        try:
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(line.encode(encoding, errors="backslashreplace").decode(encoding), flush=True)
        except (OSError, UnicodeError, ValueError):
            pass


def _console(message, prefix="S2T"):
    global _console_started
    if not _console_started:
        try:
            _native_start_thread(_write_console, ())
        except RuntimeError:
            return
        _console_started = True
    _console_pending.append(f"[{prefix}] {_safe_log_line(message, _token)}")


class _RuntimeLog(list):
    def __init__(self, token):
        super().__init__()
        self.token = token
        self.stop_requested = False

    def append(self, line):
        safe_line = _safe_log_line(line, self.token)
        super().append(safe_line)
        _console(safe_line, prefix="S2T runtime")


def _stream_runtime_logs(proc, buffer):
    _console(f"Output reader started: PID {proc.pid}")
    try:
        model_routes.stream_logs(proc, buffer)
    finally:
        if buffer.stop_requested:
            _console(f"Output reader ended after requested stop: PID {proc.pid}")
        else:
            _console(f"Output reader ended: PID {proc.pid}, exit_code={proc.poll()}")
        proc.stdout.close()


def _launch_runtime(command, **kwargs):
    # Windows speech pipes use stdlib Popen; blocking creation stays off the hub.
    if os.name == "nt":
        return get_hub().threadpool.apply(lambda: _native_popen(command, **kwargs))
    return subprocess.Popen(command, **kwargs)


def _stop_runtime(proc, *, shutdown=False):
    # Native Windows wait() blocks. At interpreter exit, no live hub is required.
    if os.name == "nt" and not shutdown:
        get_hub().threadpool.apply(model_routes.safe_stop, (proc,))
    else:
        model_routes.safe_stop(proc)
    if proc.stdin and (shutdown or proc.poll() is not None):
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass


def _runtime_config():
    path = str(get_setting("speech.s2t.runtime_path", default="") or "").strip()
    if not path:
        raise ValueError("Configure the Speech runtime path in Admin Settings first.")
    if not os.path.isabs(path):
        path = os.path.join(model_routes.BASE_DIR, path)
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ValueError("Speech runtime interpreter was not found. Check Admin Settings.")
    port = int(get_setting("speech.s2t.port", default=8082, cast=int))
    app_port = int(current_app.config.get("BOOTSTRAP_CONFIG", {}).get("app_port", 5000))
    if not 1 <= port <= 65535 or port in {app_port, get_llama_main_port(), get_llama_title_port()}:
        raise ValueError("Choose a separate speech service port between 1 and 65535.")
    return path, port


def _selected_model(path):
    row = model_routes._find_registry_model_by_path(path)
    if not row or not all(model_routes._as01(row.get(key)) for key in ("is_s2t", "is_enabled", "is_present")) or model_routes._as01(row.get("is_projector")):
        raise ValueError("Select an enabled, present Speech-to-Text model from Model Management.")
    resolved = model_routes.resolve_model_path(row["model_path"])
    if not os.path.isfile(resolved):
        raise ValueError("The selected speech model file is missing.")
    if not resolved.lower().endswith(".nemo"):
        raise ValueError("This speech runtime requires a local NeMo .nemo checkpoint.")
    return row, resolved


def _refresh_process():
    if _process is not None and _process.poll() is not None and _state["status"] != "error":
        phase = "during startup" if _state["status"] == "starting" else "while running"
        _console(f"Child exited {phase}: {_process.returncode}")
        _state.update(status="error", device="", message="Speech runtime exited. Check the runtime log, configuration and available GPU memory.")


def _snapshot():
    with _lock:
        _refresh_process()
        return dict(_state)


def _local_request(method, path, *, port, token, **kwargs):
    with requests.Session() as client:
        client.trust_env = False
        return client.request(method, f"http://127.0.0.1:{port}{path}",
                              headers={"Authorization": f"Bearer {token}"}, allow_redirects=False, **kwargs)


def _monitor(proc, port, token, started_at):
    deadline = started_at + 300
    while time.monotonic() < deadline:
        with _lock:
            if _process is not proc:
                return
            _refresh_process()
            if proc.poll() is not None:
                return
        try:
            response = _local_request("GET", "/health", port=port, token=token, timeout=2)
            payload = response.json() if response.status_code == 200 else {}
            response.close()
            if isinstance(payload, dict) and payload.get("status") == "ready":
                with _lock:
                    if _process is proc and proc.poll() is None:
                        _state.update(status="running", device=str(payload.get("device", ""))[:120], message="Ready for dictation.")
                        _console(f"Ready on {_state['device']} ({time.monotonic() - started_at:.1f}s)")
                return
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1)
    with _lock:
        if _process is proc:
            _console("Startup timeout after 300s")
            _stop_runtime(proc)
            _state.update(status="error", message="Speech startup timed out after 5 minutes. Check the runtime log and configuration.")


def _shutdown():
    if _process is not None:
        _stop_runtime(_process, shutdown=True)


atexit.register(_shutdown)


@speech_routes.route("/status", methods=["GET"])
@login_required()
def status():
    payload = _snapshot()
    payload["selected_model"] = str(get_setting("speech.s2t.model_path", default="") or "")
    payload["max_seconds"] = 120
    return jsonify(payload)


@speech_routes.route("/models", methods=["GET"])
@login_required(role="admin")
def models():
    rows = model_routes.registry_list(include_disabled=True)
    return jsonify({"models": [
        {"value": row["model_path"], "name": row.get("friendly_name") or row["model_name"]}
        for row in rows if all(model_routes._as01(row.get(key)) for key in ("is_s2t", "is_enabled", "is_present"))
        and not model_routes._as01(row.get("is_projector"))
    ], "selected_model": str(get_setting("speech.s2t.model_path", default="") or "")})


@speech_routes.route("/select", methods=["POST"])
@login_required(role="admin")
def select_model():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("model_path"), str):
        return jsonify(message="Select a speech model."), 400
    with _lock:
        _refresh_process()
        if _process is not None and _process.poll() is None:
            return jsonify(message="Stop the speech runtime before changing its model."), 409
        path = data["model_path"].strip()
        try:
            if path:
                row, _ = _selected_model(path)
                path = row["model_path"]
        except ValueError as exc:
            return jsonify(message=str(exc)), 400
        set_setting("speech.s2t.model_path", path, "string", updated_by_user_id=session["user_id"])
    return jsonify(status="success")


@speech_routes.route("/start", methods=["POST"])
@login_required(role="admin")
def start():
    global _process, _port, _token, _logs
    with _lock:
        if _process is not None and _process.poll() is None:
            return jsonify(message="Speech runtime is already running or starting."), 409
        try:
            executable, port = _runtime_config()
            row, model_path = _selected_model(get_setting("speech.s2t.model_path", default=""))
        except (ValueError, TypeError) as exc:
            _console(f"Launch failed: {exc}")
            return jsonify(message=str(exc)), 400
        _console(f"Start requested: {row.get('friendly_name') or row['model_name']}")
        token = secrets.token_urlsafe(32)
        env = os.environ.copy()
        # The configured interpreter owns its Python environment, independently of CE.
        for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
            env.pop(name, None)
        env.update(CE_SPEECH_TOKEN=token, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                   HF_HUB_DISABLE_TELEMETRY="1", PYTHONUNBUFFERED="1")
        runtime_cwd = os.path.dirname(executable)
        _logs = _RuntimeLog(token)
        _console(f"CE Python: {sys.executable}; prefix: {sys.prefix}; base prefix: {sys.base_prefix}")
        _console(f"Launching {executable} on port {port}")
        _console(f"Adapter: {os.path.join(model_routes.BASE_DIR, 'speech_runtime.py')}; model: {model_path}; cwd: {runtime_cwd}")
        _console("I/O: stdin=PIPE, stdout=PIPE, stderr=STDOUT; shell=False; creationflags=0; HF_HUB_OFFLINE=1; TRANSFORMERS_OFFLINE=1")
        started_at = time.monotonic()
        try:
            proc = _launch_runtime(
                [executable, "-u", os.path.join(model_routes.BASE_DIR, "speech_runtime.py"),
                 "--model", model_path, "--port", str(port)],
                cwd=runtime_cwd, env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", bufsize=1, close_fds=True,
            )
        except OSError as exc:
            _console(f"Launch failed: {type(exc).__name__}, errno={exc.errno}, winerror={getattr(exc, 'winerror', None)}")
            _state.update(status="error", message="Speech runtime could not start. Check its interpreter path and permissions.")
            return jsonify(_state), 500
        _process, _port, _token = proc, port, token
        _state.update(status="starting", model=row.get("friendly_name") or row["model_name"], device="", message="Loading speech model…")
        _console(f"Child PID {proc.pid}")
        try:
            if os.name == "nt":
                # Exactly one native reader; never read a native pipe on the hub.
                _native_start_thread(_stream_runtime_logs, (proc, _logs))
            else:
                threading.Thread(target=_stream_runtime_logs, args=(proc, _logs), daemon=True).start()
            threading.Thread(target=_monitor, args=(proc, port, token, started_at), daemon=True).start()
        except RuntimeError:
            _console("Launch failed: could not start speech output reader or monitor")
            _stop_runtime(proc)
            _state.update(status="error", message="Speech runtime supervision could not start. Check the CE console.")
            return jsonify(_state), 500
        return jsonify(_state), 202


@speech_routes.route("/stop", methods=["POST"])
@login_required(role="admin")
def stop():
    global _process, _token
    _console("Stop requested")
    with _lock:
        if _process is not None:
            if _process.poll() is None:
                _logs.stop_requested = True
            _stop_runtime(_process)
            if _process.poll() is None:
                _logs.stop_requested = False
                _console("Runtime did not stop; check the owned process on the host")
                return jsonify(message="Speech runtime did not stop. Check the process on the host."), 500
        _process, _token = None, ""
        _state.update(status="stopped", model="", device="", message="Speech-to-text model is not running.")
        _console("Runtime stopped")
    return jsonify(_state)


@speech_routes.route("/logs", methods=["GET"])
@login_required(role="admin")
def logs():
    return jsonify(lines=list(_logs[-100:]))


@speech_routes.route("/transcribe", methods=["POST"])
@login_required()
def transcribe():
    with _lock:
        _refresh_process()
        if _state["status"] != "running":
            return jsonify(message="Speech-to-text model is not running."), 409
        proc, port, token = _process, _port, _token
    if request.mimetype != "audio/wav":
        return jsonify(message="Dictation requires WAV audio."), 400
    if request.content_length is not None and request.content_length > MAX_AUDIO_BYTES:
        return jsonify(message="Dictation is limited to 2 minutes."), 413
    if not _transcription_lock.acquire(blocking=False):
        return jsonify(message="Speech runtime is busy. Try again when the current dictation finishes."), 409
    response = None
    try:
        audio = request.stream.read(MAX_AUDIO_BYTES + 1)
        if not audio or len(audio) > MAX_AUDIO_BYTES:
            return jsonify(message="Dictation is empty or exceeds 2 minutes."), 400
        response = _local_request("POST", "/transcribe", port=port, token=token,
                                  data=audio, timeout=(3, 180))
        if response.status_code != 200:
            code = response.status_code if response.status_code in (400, 409, 413) else 502
            return jsonify(message="Speech could not be transcribed. Check the recording and speech runtime status."), code
        payload = response.json()
        text = payload.get("text") if isinstance(payload, dict) else None
        with _lock:
            if _process is not proc or proc.poll() is not None:
                return jsonify(message="Speech runtime stopped during transcription."), 409
        if not isinstance(text, str) or len(text) > 20000:
            raise ValueError("Invalid speech response")
        return jsonify(text=text.strip())
    except (requests.RequestException, ValueError):
        return jsonify(message="Speech runtime did not return a valid transcription. Check its status and try again."), 502
    finally:
        if response is not None:
            response.close()
        _transcription_lock.release()
