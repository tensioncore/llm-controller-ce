# benchmark_routes.py
from flask import Blueprint, request, jsonify, session
import os
import time
import json
import threading
import datetime
import traceback

import requests

from db_mysql import mysql_conn  # ✅ Phase A5: unified MySQL entrypoint
from auth import login_required
from extensions import socketio
from helpers import find_models

import model_routes  # reuse llama-server launch helpers + constants

benchmark_routes = Blueprint("benchmark_routes", __name__)

# -------------------------------------------------------------------
# Globals (single-run worker)
# -------------------------------------------------------------------
_bench_lock = threading.Lock()

# Cancel flag (threads can’t be killed safely; use a cooperative stop)
_bench_cancel = threading.Event()

_bench_state = {
    "running": False,
    "started_at": None,
    "ended_at": None,
    "status": "idle",  # idle|running|done|error|stopping
    "message": "",
    "current_model": "",
    "current_model_idx": 0,
    "total_models": 0,
    "current_prompt_idx": 0,
    "total_prompts": 0,
    "current_run_id": None,
    "skipped_models": 0,
    "benchmarked_models": 0,
    "failed_models": 0,
}

CE_BENCHMARK_PROFILE_NAME = "Default v1"
CE_BENCHMARK_PROMPTSET_NAME = "Core Suite"
CE_BENCHMARK_PROMPTSET_VERSION = "v1"
CE_BENCHMARK_PROMPT_LIMIT = 5
CE_PROMPTS_INVALIDATED_FAIL_REASON = "ce_prompts_updated"

# -------------------------------------------------------------------
# DB helpers (DO NOT use flask.g in background threads)
# -------------------------------------------------------------------
def _emit_to_user(user_id, event, payload):
    # Your chat system already uses user_{user_id} rooms.
    # NOTE: The UI may not always be joined to this room (esp. after refresh),
    # so the drawer uses /status polling for truth. Emits are "nice to have".
    try:
        if user_id:
            try:
                from chat_routes import prune_stale_socket_rooms
                prune_stale_socket_rooms(user_id)
            except Exception:
                pass
            socketio.emit(event, payload, room=f"user_{user_id}")
        else:
            socketio.emit(event, payload)
    except Exception:
        pass

def _now_dt():
    return datetime.datetime.now()

def _fmt_ts(ts_int):
    try:
        return datetime.datetime.fromtimestamp(int(ts_int)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""

def _norm_model_key(s: str) -> str:
    """
    Normalize a model name/path into a comparable key.
    - lowercased
    - slashes normalized
    - trim whitespace
    """
    if s is None:
        return ""
    try:
        s = str(s).strip()
    except Exception:
        return ""
    s = s.replace("\\", "/").strip().lower()
    return s

def _basename_key(s: str) -> str:
    """
    Return the last path component (folder/file name) as a normalized key.
    """
    s = _norm_model_key(s)
    if not s:
        return ""
    parts = [p for p in s.split("/") if p]
    return parts[-1] if parts else s

# -------------------------------------------------------------------
# Load the seeded CE prompt set
# -------------------------------------------------------------------
def get_ce_prompt_bundle():
    prompt_set = get_prompt_set(CE_BENCHMARK_PROMPTSET_NAME, CE_BENCHMARK_PROMPTSET_VERSION)
    if not prompt_set:
        return None
    prompt_set["prompts"] = list((prompt_set.get("prompts") or [])[:CE_BENCHMARK_PROMPT_LIMIT])
    return prompt_set


def _coerce_profile_setting(settings, key, cast, minimum=None, maximum=None, alt_keys=None):
    keys = [key] + list(alt_keys or [])
    raw_value = None
    for candidate in keys:
        candidate_value = settings.get(candidate)
        if candidate_value is None or candidate_value == "":
            continue
        raw_value = candidate_value
        break

    if raw_value is None:
        raise RuntimeError(f"Benchmark profile missing required setting: {key}")

    try:
        if cast is bool:
            if isinstance(raw_value, str):
                parsed = raw_value.strip().lower() in ("1", "true", "yes", "on")
            else:
                parsed = bool(raw_value)
        else:
            parsed = cast(raw_value)
    except Exception as e:
        raise RuntimeError(f"Benchmark profile has invalid setting: {key}") from e

    if minimum is not None and parsed < minimum:
        raise RuntimeError(f"Benchmark profile has invalid setting: {key}")
    if maximum is not None and parsed > maximum:
        raise RuntimeError(f"Benchmark profile has invalid setting: {key}")
    return parsed

# -------------------------------------------------------------------
# Clear results for specific model
# IMPORTANT:
# Reset ALWAYS clears ALL benchmark runs for a model.
# There is intentionally NO failed-only mode.
# -------------------------------------------------------------------
@benchmark_routes.route("/reset_model", methods=["POST"])
@login_required(role="admin")
def bench_reset_model():
    data = request.get_json() or {}
    fingerprint = (data.get("fingerprint") or "").strip().lower()

    # Don't allow reset while benchmarking is running
    with _bench_lock:
        if _bench_state.get("running"):
            return jsonify({"status": "error", "message": "Benchmarks are running. Stop them first."}), 400

    # STRICT: 40-char hex only (per your spec)
    if not fingerprint or len(fingerprint) != 40:
        return jsonify({"status": "error", "message": "Invalid fingerprint length (must be 40 hex chars)."}), 400
    try:
        int(fingerprint, 16)
    except Exception:
        return jsonify({"status": "error", "message": "Invalid fingerprint format (hex required)."}), 400

    db = mysql_conn()
    cur = db.cursor(dictionary=True)
    try:
        # Resolve model by fingerprint
        cur.execute("SELECT id FROM llm_benchmark_models WHERE fingerprint=%s LIMIT 1", (fingerprint,))
        row = cur.fetchone()
        if not row or not row.get("id"):
            return jsonify({"status": "error", "message": "Fingerprint not found in registry."}), 404

        model_id = int(row["id"])

        # ✅ ALWAYS clear ALL runs (no failed-only logic)
        cur.execute("SELECT id FROM llm_benchmark_runs WHERE model_id=%s", (model_id,))
        run_ids = [int(r["id"]) for r in (cur.fetchall() or [])]

        if not run_ids:
            return jsonify({"status": "success", "message": "No runs to clear for this model.", "cleared_runs": 0})

        placeholders = ",".join(["%s"] * len(run_ids))
        cur2 = db.cursor()
        try:
            cur2.execute(f"DELETE FROM llm_benchmark_results WHERE run_id IN ({placeholders})", tuple(run_ids))
            cur2.execute(f"DELETE FROM llm_benchmark_runs WHERE id IN ({placeholders})", tuple(run_ids))
            db.commit()
        finally:
            try: cur2.close()
            except Exception: pass

        return jsonify({"status": "success", "message": "Cleared benchmark history for model.", "cleared_runs": len(run_ids)})
    finally:
        try:
            cur.close()
            db.close()
        except Exception:
            pass


# -------------------------------------------------------------------
# Model + run DB helpers
# -------------------------------------------------------------------
def _get_registry_models_for_bench(include_disabled: bool = False):
    """
    Pull models ONLY from the registry table.
    - present on disk
    - enabled (unless include_disabled=True)
    - allow_benchmark=1
    """
    db = mysql_conn()
    cur = db.cursor(dictionary=True)
    try:
        where = ["is_present=1", "allow_benchmark=1"]
        if not include_disabled:
            where.append("is_enabled=1")

        cur.execute(f"""
            SELECT
              id, model_name, model_path, file_size, mtime, fingerprint,
              is_enabled, is_present, allow_benchmark
            FROM llm_benchmark_models
            WHERE {" AND ".join(where)}
            ORDER BY model_name ASC
        """)
        return cur.fetchall() or []
    finally:
        try:
            cur.close()
            db.close()
        except Exception:
            pass

def _abs_model_path_from_registry(model_path: str) -> str:
    """
    Registry stores model_path as relative within the configured scan directory.
    Convert it into a normalized absolute path.
    """
    rel = (model_path or "").strip()
    if not rel:
        return ""
    if os.path.isabs(rel):
        return os.path.normpath(rel)
    return model_routes.resolve_model_path(rel)

def get_profile(profile_name=CE_BENCHMARK_PROFILE_NAME):
    db = mysql_conn()
    cur = db.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, name, settings_json FROM llm_benchmark_profiles WHERE name=%s LIMIT 1", (profile_name,))
        row = cur.fetchone()
        if not row:
            return None
        settings = row["settings_json"]
        if isinstance(settings, str):
            try:
                settings = json.loads(settings)
            except Exception:
                settings = {}
        return {"id": row["id"], "name": row["name"], "settings": settings or {}}
    finally:
        try:
            cur.close()
            db.close()
        except Exception:
            pass

def get_prompt_set(name=CE_BENCHMARK_PROMPTSET_NAME, version=CE_BENCHMARK_PROMPTSET_VERSION):
    db = mysql_conn()
    cur = db.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT id, name, version FROM llm_benchmark_prompt_sets WHERE name=%s AND version=%s LIMIT 1",
            (name, version),
        )
        ps = cur.fetchone()
        if not ps:
            return None

        cur.execute(
            """
            SELECT id, ordering, category, prompt_text, max_tokens
            FROM llm_benchmark_prompts
            WHERE prompt_set_id=%s
            ORDER BY ordering ASC
            """,
            (ps["id"],),
        )
        prompts = cur.fetchall() or []
        return {"id": ps["id"], "name": ps["name"], "version": ps["version"], "prompts": prompts}
    finally:
        try:
            cur.close()
            db.close()
        except Exception:
            pass

def run_exists(model_id, profile_id, prompt_set_id):
    db = mysql_conn()
    cur = db.cursor()
    try:
        cur.execute(
            """
            SELECT 1
            FROM llm_benchmark_runs
            WHERE model_id=%s AND profile_id=%s AND prompt_set_id=%s
              AND status IN ('PASS','FAILED')
              AND COALESCE(fail_reason, '') <> %s
            LIMIT 1
            """,
            (model_id, profile_id, prompt_set_id, CE_PROMPTS_INVALIDATED_FAIL_REASON),
        )
        return cur.fetchone() is not None
    finally:
        try:
            cur.close()
            db.close()
        except Exception:
            pass

def create_run(model_id, profile_id, prompt_set_id):
    db = mysql_conn()
    cur = db.cursor()
    try:
        cur.execute(
            """
            INSERT INTO llm_benchmark_runs (model_id, profile_id, prompt_set_id, status, started_at)
            VALUES (%s, %s, %s, 'RUNNING', NOW())
            """,
            (model_id, profile_id, prompt_set_id),
        )
        db.commit()
        return cur.lastrowid
    finally:
        try:
            cur.close()
            db.close()
        except Exception:
            pass

def finish_run(run_id, status, fail_reason=None, fail_prompt_id=None):
    db = mysql_conn()
    cur = db.cursor()
    try:
        cur.execute(
            """
            UPDATE llm_benchmark_runs
            SET status=%s, fail_reason=%s, fail_prompt_id=%s, ended_at=NOW()
            WHERE id=%s
            """,
            (status, fail_reason, fail_prompt_id, int(run_id)),
        )
        db.commit()
    finally:
        try:
            cur.close()
            db.close()
        except Exception:
            pass

def insert_result(run_id, prompt_id, success, response_text, metrics, error=None):
    db = mysql_conn()
    cur = db.cursor()
    try:
        cur.execute(
            """
            INSERT INTO llm_benchmark_results
              (run_id, prompt_id, success, error, response_text,
               tokens_prompt, tokens_generated, ttft_ms,
               prompt_eval_tps, eval_tps, total_ms)
            VALUES
              (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(run_id),
                int(prompt_id),
                1 if success else 0,
                error,
                response_text,
                int(metrics.get("tokens_prompt", 0)),
                int(metrics.get("tokens_generated", 0)),
                int(metrics.get("ttft_ms", 0)),
                float(metrics.get("prompt_eval_tps", 0)),
                float(metrics.get("eval_tps", 0)),
                int(metrics.get("total_ms", 0)),
            ),
        )
        db.commit()
    finally:
        try:
            cur.close()
            db.close()
        except Exception:
            pass

# -------------------------------------------------------------------
# llama-server orchestration (reuse model_routes)
# We intentionally FORCE stop any running main/title model before starting.
# -------------------------------------------------------------------
def stop_any_running_models():
    try:
        model_routes._stop_main_model_runtime(clear_logs=True)
        model_routes._stop_title_model_runtime(clear_logs=True)
    except Exception:
        pass

def start_benchmark_model(model_path, settings, force_cpu_only=False):
    """
    Override the legacy benchmark launcher with the same split-GGUF and CPU-only
    normalization used by the main model start path.
    """
    normalized_model_path, split_info, split_error = model_routes._normalize_model_launch_path(model_path)
    if split_error:
        raise RuntimeError(split_error)

    n_gpu_layers = _coerce_profile_setting(settings, "n_gpu_layers", int, minimum=0, maximum=1000)
    n_threads = _coerce_profile_setting(settings, "n_threads", int, minimum=1, maximum=256, alt_keys=("n_cpu_threads",))
    cpu_only = bool(force_cpu_only or n_gpu_layers == 0)
    effective_n_gpu_layers = 0 if cpu_only else n_gpu_layers

    temperature = _coerce_profile_setting(settings, "temperature", float, minimum=0.0, maximum=2.0)
    top_k = _coerce_profile_setting(settings, "top_k", int, minimum=0, maximum=1000)
    top_p = _coerce_profile_setting(settings, "top_p", float, minimum=0.0, maximum=1.0)
    repeat_penalty = _coerce_profile_setting(settings, "repeat_penalty", float, minimum=1.0, maximum=5.0)
    seed = _coerce_profile_setting(settings, "seed", int, minimum=0, maximum=2**31 - 1)

    model_settings = {
        "temp": temperature,
        "top-k": top_k,
        "top-p": top_p,
        "repeat-penalty": repeat_penalty,
        "seed": seed,
    }

    exe_path = model_routes._resolve_llama_server_executable()
    launch_plan = model_routes.build_llama_launch_plan(
        normalized_model_path,
        effective_n_gpu_layers,
        executable_path=exe_path,
    )
    capabilities = launch_plan["capabilities"]
    extra_flags = list(launch_plan["extra_flags"])
    only_one_gpu = bool(launch_plan["only_one_gpu"])
    cpu_only = bool(launch_plan["cpu_only"])
    effective_n_gpu_layers = int(launch_plan["n_gpu_layers"])

    model_routes._stop_main_model_runtime(clear_logs=True)

    model_routes.current_main_model_used = normalized_model_path
    model_routes.current_main_model_settings = model_settings
    model_routes.current_main_model_runtime = {}

    print(
        f"[BENCH] Starting model: {model_routes._get_user_facing_model_name(normalized_model_path)} "
        f"{model_routes._describe_launch_plan(launch_plan, mode='benchmark')}"
    )

    proc = model_routes.launch_llama_instance(
        model_path=normalized_model_path,
        gpu_id=0,
        port=model_routes.get_llama_main_port(),
        n_gpu_layers=effective_n_gpu_layers,
        n_threads=n_threads,
        extra_flags=extra_flags,
        model_settings=model_settings,
        only_one_gpu=only_one_gpu,
        cpu_only=cpu_only,
        executable_path=exe_path,
        runtime_capabilities=capabilities,
    )

    threading.Thread(
        target=model_routes.stream_logs,
        args=(proc, model_routes.main_log_buffer),
        daemon=True,
    ).start()

    model_routes.main_process = proc
    return {
        "proc": proc,
        "model_path": normalized_model_path,
        "display_name": model_routes._get_user_facing_model_name(normalized_model_path),
        "cpu_only": cpu_only,
        "n_gpu_layers": effective_n_gpu_layers,
        "n_threads": n_threads,
        "capabilities": capabilities,
    }


def wait_for_server_ready(timeout_sec=120):
    if _bench_cancel.is_set():
        return False, "cancelled", "cancelled", False
    return model_routes.wait_for_llama_server_ready(
        model_routes.main_process,
        model_routes.get_llama_main_port(),
        timeout=timeout_sec,
        log_buffer=model_routes.main_log_buffer,
    )

def call_completion(prompt_text, max_tokens, timeout_sec):
    url = model_routes.get_llama_main_completion_url()
    payload = {
        "model": "llama",
        "messages": [{"role": "user", "content": prompt_text}],
        "stream": False,
        "max_tokens": int(max_tokens),
    }
    t0 = time.time()
    r = requests.post(url, json=payload, timeout=int(timeout_sec))
    dt = time.time() - t0
    ms = int(round(dt * 1000))

    if r.status_code == 503:
        raise RuntimeError("Model unavailable (503)")
    r.raise_for_status()

    j = r.json()
    choice = (j.get("choices") or [{}])[0] or {}
    msg = choice.get("message") or {}
    text = ""
    if isinstance(msg, dict):
        text = msg.get("content") or ""
    if not text:
        text = choice.get("text") or ""
    if not text:
        text = j.get("content") or j.get("response") or ""

    usage = j.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or usage.get("generated_tokens") or 0)

    if completion_tokens <= 0 and text:
        completion_tokens = max(1, len(text.split()))
    if prompt_tokens <= 0:
        prompt_tokens = max(1, len(prompt_text.split()))

    eval_tps = 0.0
    if ms > 0:
        eval_tps = round(completion_tokens / (ms / 1000.0), 4)

    metrics = {
        "tokens_prompt": prompt_tokens,
        "tokens_generated": completion_tokens,
        "ttft_ms": 0,
        "prompt_eval_tps": 0.0,
        "eval_tps": float(eval_tps),
        "total_ms": ms,
    }
    return text.strip(), metrics

# -------------------------------------------------------------------
# Worker
# -------------------------------------------------------------------
def _set_state(**kwargs):
    for k, v in kwargs.items():
        _bench_state[k] = v

def _log_state_tick():
    # Compact CMD line so you can watch it run without spamming too hard
    try:
        s = dict(_bench_state)
        print(
            f"[BENCH] {s.get('status')} | "
            f"model {s.get('current_model_idx')}/{s.get('total_models')} "
            f"prompt {s.get('current_prompt_idx')}/{s.get('total_prompts')} | "
            f"pass={s.get('benchmarked_models')} fail={s.get('failed_models')} skip={s.get('skipped_models')} | "
            f"{s.get('current_model')}"
        )
    except Exception:
        pass

def _cancel_and_exit(admin_user_id, run_id=None, model_path=None, where=""):
    """
    Handles benchmark cancellation consistently.
    - Stops llama-server immediately (already done by /stop, but safe here too)
    - Marks current run FAILED(cancelled) if run_id is valid
    - Updates bench state + emits done
    """
    try:
        stop_any_running_models()
    except Exception:
        pass

    try:
        if run_id:
            finish_run(run_id, "FAILED", fail_reason="cancelled", fail_prompt_id=None)
    except Exception:
        pass

    with _bench_lock:
        _set_state(
            running=False,
            ended_at=int(time.time()),
            status="idle",
            message=f"Benchmarks cancelled{(' (' + where + ')') if where else ''}.",
        )

    _emit_to_user(admin_user_id, "benchmark_done", dict(_bench_state))
    print(f"[BENCH] ===== BENCHMARK CANCELLED ===== { _fmt_ts(_bench_state['ended_at']) }")
    return

def benchmark_worker(admin_user_id, profile_name, promptset_name, promptset_version, force=False):
    # ✅ ensure clean cancel flag for a new worker start
    _bench_cancel.clear()

    with _bench_lock:
        _set_state(
            running=True,
            started_at=int(time.time()),
            ended_at=None,
            status="running",
            message="Starting benchmarks...",
            current_model="",
            current_model_idx=0,
            total_models=0,
            current_prompt_idx=0,
            total_prompts=0,
            current_run_id=None,
            skipped_models=0,
            benchmarked_models=0,
            failed_models=0,
        )

    print(f"[BENCH] ===== BENCHMARK START ===== { _fmt_ts(_bench_state['started_at']) }")
    print(f"[BENCH] Profile={profile_name} PromptSet={promptset_name} Version={promptset_version} Force={force}")

    try:

        # ✅ Source of truth: registry ONLY (present + enabled + allow_benchmark=1)
        ggufs = _get_registry_models_for_bench(include_disabled=False)

        prof = get_profile(profile_name) or get_profile(CE_BENCHMARK_PROFILE_NAME)
        if not prof:
            raise RuntimeError(f"No benchmark profile found ({CE_BENCHMARK_PROFILE_NAME} missing).")

        is_ce_prompt_bundle = (
            (promptset_name or CE_BENCHMARK_PROMPTSET_NAME) == CE_BENCHMARK_PROMPTSET_NAME and
            (promptset_version or CE_BENCHMARK_PROMPTSET_VERSION) == CE_BENCHMARK_PROMPTSET_VERSION
        )
        ps = get_prompt_set(promptset_name, promptset_version)
        if not ps and is_ce_prompt_bundle:
            ps = get_ce_prompt_bundle()
        if not ps:
            ps = get_prompt_set(CE_BENCHMARK_PROMPTSET_NAME, CE_BENCHMARK_PROMPTSET_VERSION)
        if not ps:
            raise RuntimeError(f"No prompt set found ({CE_BENCHMARK_PROMPTSET_NAME} {CE_BENCHMARK_PROMPTSET_VERSION} missing).")

        prompts = ps["prompts"] or []
        if is_ce_prompt_bundle:
            prompts = prompts[:CE_BENCHMARK_PROMPT_LIMIT]
        if not prompts:
            raise RuntimeError("Prompt set has no prompts.")

        with _bench_lock:
            _set_state(
                total_models=len(ggufs),
                total_prompts=len(prompts),
                message=(
                    f"Loaded {len(ggufs)} models and {len(prompts)} CE benchmark prompts."
                    if is_ce_prompt_bundle else
                    f"Loaded {len(ggufs)} models; prompt set {ps['name']} {ps['version']} with {len(prompts)} prompts."
                ),
            )
        _emit_to_user(admin_user_id, "benchmark_status", dict(_bench_state))
        _log_state_tick()

        settings = prof["settings"] or {}
        per_prompt_timeout = _coerce_profile_setting(settings, "per_prompt_timeout_sec", int, minimum=1)
        warmup_enabled = _coerce_profile_setting(settings, "warmup_enabled", bool)
        warmup_max_tokens = _coerce_profile_setting(settings, "warmup_max_tokens", int, minimum=1)

        # ✅ Global token cap
        max_tokens_cap = int(settings.get("max_tokens_cap", 2500))

        stop_any_running_models()

        for idx, m in enumerate(ggufs, start=1):
            if _bench_cancel.is_set():
                return _cancel_and_exit(admin_user_id, run_id=_bench_state.get("current_run_id"), where="before model start")

            rel_path = (m.get("model_path") or "").strip()
            model_path = _abs_model_path_from_registry(rel_path)
            display_name = (m.get("model_name") or "").strip() or model_routes._get_user_facing_model_name(model_path)

            if not rel_path or not model_path:
                with _bench_lock:
                    _bench_state["failed_models"] += 1
                    _bench_state["message"] = "FAILED (empty model_path in registry row)"
                _emit_to_user(admin_user_id, "benchmark_progress", dict(_bench_state))
                continue

            with _bench_lock:
                _set_state(current_model_idx=idx, current_model=display_name, current_prompt_idx=0)
            _emit_to_user(admin_user_id, "benchmark_progress", dict(_bench_state))
            _log_state_tick()

            # ✅ Registry is source of truth now — don't fingerprint/upsert here.
            # Just make sure the file actually exists on disk.
            if not os.path.isfile(model_path):
                with _bench_lock:
                    _bench_state["failed_models"] += 1
                    show_name = rel_path or os.path.basename(model_path)
                    _bench_state["message"] = f"FAILED (missing file on disk): {show_name}"
                _emit_to_user(admin_user_id, "benchmark_progress", dict(_bench_state))
                continue

            # model_id already came from registry query
            model_id = int(m.get("id") or 0)
            if model_id <= 0:
                with _bench_lock:
                    _bench_state["failed_models"] += 1
                    _bench_state["message"] = f"FAILED (invalid model id): {rel_path or os.path.basename(model_path)}"
                _emit_to_user(admin_user_id, "benchmark_progress", dict(_bench_state))
                continue

            if (not force) and run_exists(model_id, prof["id"], ps["id"]):
                with _bench_lock:
                    _bench_state["skipped_models"] += 1
                    _bench_state["message"] = f"Skipped (already benchmarked): {display_name}"
                _emit_to_user(admin_user_id, "benchmark_progress", dict(_bench_state))
                _log_state_tick()
                continue

            run_id = create_run(model_id, prof["id"], ps["id"])
            with _bench_lock:
                _set_state(current_run_id=run_id, message=f"Benchmarking: {display_name}", current_prompt_idx=0)
            _emit_to_user(admin_user_id, "benchmark_progress", dict(_bench_state))
            _log_state_tick()

            try:
                if _bench_cancel.is_set():
                    return _cancel_and_exit(admin_user_id, run_id=run_id, where="after run created")

                model_routes.safe_stop(model_routes.main_process)
                model_routes.main_process = None

                launch_info = start_benchmark_model(model_path, settings)

                if _bench_cancel.is_set():
                    return _cancel_and_exit(admin_user_id, run_id=run_id, where="after model launch")

                ready, ready_error, ready_code, retry_cpu_only = wait_for_server_ready(timeout_sec=180)
                if _bench_cancel.is_set():
                    return _cancel_and_exit(admin_user_id, run_id=run_id, where="during ready wait")

                if (not ready) and retry_cpu_only and not launch_info["cpu_only"]:
                    with _bench_lock:
                        _bench_state["message"] = f"Retrying on CPU: {display_name}"
                    _emit_to_user(admin_user_id, "benchmark_progress", dict(_bench_state))

                    model_routes.safe_stop(model_routes.main_process)
                    model_routes.main_process = None

                    launch_info = start_benchmark_model(model_path, settings, force_cpu_only=True)

                    if _bench_cancel.is_set():
                        return _cancel_and_exit(admin_user_id, run_id=run_id, where="during cpu-only retry")

                    ready, ready_error, ready_code, retry_cpu_only = wait_for_server_ready(timeout_sec=180)
                    if _bench_cancel.is_set():
                        return _cancel_and_exit(admin_user_id, run_id=run_id, where="during cpu-only ready wait")

                if not ready:
                    finish_run(run_id, "FAILED", fail_reason=ready_code or "model_load_timeout", fail_prompt_id=None)
                    model_routes.safe_stop(model_routes.main_process)
                    model_routes.main_process = None
                    with _bench_lock:
                        _bench_state["failed_models"] += 1
                        _bench_state["message"] = f"FAILED ({ready_code or 'load_timeout'}): {display_name}"
                    _emit_to_user(admin_user_id, "benchmark_progress", dict(_bench_state))
                    _log_state_tick()
                    continue

                model_routes.current_main_model_runtime = model_routes._build_runtime_state(
                    launch_info.get("capabilities"),
                    int(launch_info["n_gpu_layers"]),
                    int(launch_info["n_threads"]),
                    bool(launch_info["cpu_only"]),
                )

                if warmup_enabled and not _bench_cancel.is_set():
                    try:
                        call_completion("Say OK.", warmup_max_tokens, timeout_sec=60)
                    except Exception:
                        pass

                if _bench_cancel.is_set():
                    return _cancel_and_exit(admin_user_id, run_id=run_id, where="after warmup")

                model_failed = False
                fail_reason = None
                fail_prompt_id = None

                for pidx, p in enumerate(prompts, start=1):
                    if _bench_cancel.is_set():
                        model_failed = True
                        fail_reason = "cancelled"
                        fail_prompt_id = int(p["id"])
                        break

                    with _bench_lock:
                        _set_state(current_prompt_idx=pidx, message=f"Running prompt {pidx}/{len(prompts)}")
                    _emit_to_user(admin_user_id, "benchmark_progress", dict(_bench_state))

                    prompt_id = int(p["id"])
                    prompt_text = p["prompt_text"]

                    db_prompt_max = int(p.get("max_tokens") or 256)
                    max_tokens = min(db_prompt_max, max_tokens_cap)

                    try:
                        print(f"[BENCH]   Prompt {pidx}/{len(prompts)} (max_tokens={max_tokens})")
                        text, metrics = call_completion(prompt_text, max_tokens, timeout_sec=per_prompt_timeout)

                        if _bench_cancel.is_set():
                            model_failed = True
                            fail_reason = "cancelled"
                            fail_prompt_id = prompt_id
                            break

                        insert_result(run_id, prompt_id, True, text, metrics, error=None)
                        _log_state_tick()

                    except requests.exceptions.Timeout:
                        model_failed = True
                        fail_reason = "prompt_timeout"
                        fail_prompt_id = prompt_id
                        insert_result(run_id, prompt_id, False, "", {"total_ms": per_prompt_timeout * 1000}, error="timeout")
                        print(f"[BENCH]   FAIL prompt timeout on prompt_id={prompt_id}")
                        break
                    except Exception as e:
                        model_failed = True
                        fail_reason = "prompt_error"
                        fail_prompt_id = prompt_id
                        insert_result(run_id, prompt_id, False, "", {"total_ms": 0}, error=str(e)[:255])
                        print(f"[BENCH]   FAIL prompt error on prompt_id={prompt_id}: {e}")
                        break

                if model_failed:
                    if fail_reason == "cancelled":
                        finish_run(run_id, "FAILED", fail_reason="cancelled", fail_prompt_id=fail_prompt_id)
                        return _cancel_and_exit(admin_user_id, run_id=run_id, where="during prompt loop")

                    finish_run(run_id, "FAILED", fail_reason=fail_reason, fail_prompt_id=fail_prompt_id)
                    with _bench_lock:
                        _bench_state["failed_models"] += 1
                        _bench_state["message"] = f"FAILED: {display_name} ({fail_reason})"
                    print(f"[BENCH] Model FAILED: {display_name} reason={fail_reason}")
                else:
                    finish_run(run_id, "PASS")
                    with _bench_lock:
                        _bench_state["benchmarked_models"] += 1
                        _bench_state["message"] = f"PASS: {display_name}"
                    print(f"[BENCH] Model PASS: {display_name}")

            finally:
                try:
                    model_routes._stop_main_model_runtime(clear_logs=False)
                except Exception:
                    pass

            _emit_to_user(admin_user_id, "benchmark_progress", dict(_bench_state))
            _log_state_tick()

        with _bench_lock:
            _set_state(
                running=False,
                ended_at=int(time.time()),
                status="done",
                message="Benchmarks completed.",
                current_prompt_idx=_bench_state.get("total_prompts", 0),
            )
        _emit_to_user(admin_user_id, "benchmark_done", dict(_bench_state))

        print(f"[BENCH] ===== BENCHMARK DONE ===== { _fmt_ts(_bench_state['ended_at']) }")
        _log_state_tick()

    except Exception as e:
        with _bench_lock:
            _set_state(
                running=False,
                ended_at=int(time.time()),
                status="error",
                message=f"Benchmark worker error: {str(e)}",
            )
        print(f"[BENCH] ===== BENCHMARK ERROR ===== {e}")
        print(traceback.format_exc())
        _emit_to_user(admin_user_id, "benchmark_error", {"error": str(e), "trace": traceback.format_exc()})
    finally:
        try:
            stop_any_running_models()
        except Exception:
            pass

# -------------------------------------------------------------------
# Routes
# -------------------------------------------------------------------
@benchmark_routes.route("/status", methods=["GET"])
@login_required(role="admin")
def bench_status():
    with _bench_lock:
        return jsonify({"status": "success", "state": dict(_bench_state)})


@benchmark_routes.route("/ce_prompts", methods=["GET"])
@login_required(role="admin")
def bench_ce_prompts():
    try:
        bundle = get_ce_prompt_bundle()
        if not bundle:
            return jsonify({"status": "error", "message": "CE benchmark prompt set not found."}), 404

        prompts = []
        for idx, prompt in enumerate(bundle["prompts"][:CE_BENCHMARK_PROMPT_LIMIT], start=1):
            prompts.append({
                "id": int(prompt["id"]),
                "ordering": int(prompt.get("ordering") or idx),
                "prompt_text": str(prompt.get("prompt_text") or ""),
            })

        return jsonify({"status": "success", "prompts": prompts})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Could not load CE benchmark prompts: {e}"}), 500


@benchmark_routes.route("/ce_prompts", methods=["POST"])
@login_required(role="admin")
def bench_save_ce_prompts():
    data = request.get_json() or {}
    incoming_prompts = data.get("prompts")

    with _bench_lock:
        if _bench_state.get("running"):
            return jsonify({"status": "error", "message": "Stop benchmarks before editing CE prompts."}), 400

    if not isinstance(incoming_prompts, list) or len(incoming_prompts) != CE_BENCHMARK_PROMPT_LIMIT:
        return jsonify({"status": "error", "message": f"Exactly {CE_BENCHMARK_PROMPT_LIMIT} CE prompts are required."}), 400

    cleaned_prompts = []
    for idx, prompt in enumerate(incoming_prompts, start=1):
        if not isinstance(prompt, dict):
            return jsonify({"status": "error", "message": f"Prompt {idx} is invalid."}), 400
        prompt_text = str(prompt.get("prompt_text") or "").strip()
        if not prompt_text:
            return jsonify({"status": "error", "message": f"Prompt {idx} cannot be empty."}), 400
        cleaned_prompts.append(prompt_text)

    db = mysql_conn()
    cur = db.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT id FROM llm_benchmark_prompt_sets WHERE name=%s AND version=%s LIMIT 1",
            (CE_BENCHMARK_PROMPTSET_NAME, CE_BENCHMARK_PROMPTSET_VERSION),
        )
        ps = cur.fetchone()
        if not ps:
            return jsonify({"status": "error", "message": "CE benchmark prompt set not found."}), 404

        cur.execute(
            """
            SELECT id, ordering, prompt_text
            FROM llm_benchmark_prompts
            WHERE prompt_set_id=%s
            ORDER BY ordering ASC, id ASC
            """,
            (int(ps["id"]),),
        )
        existing_prompts = list((cur.fetchall() or [])[:CE_BENCHMARK_PROMPT_LIMIT])
        if len(existing_prompts) < CE_BENCHMARK_PROMPT_LIMIT:
            return jsonify({"status": "error", "message": "CE benchmark prompt rows are incomplete."}), 500

        for idx, prompt_text in enumerate(cleaned_prompts, start=1):
            current_prompt = existing_prompts[idx - 1]
            cur.execute(
                """
                UPDATE llm_benchmark_prompts
                SET ordering=%s, prompt_text=%s
                WHERE id=%s
                """,
                (idx, prompt_text, int(current_prompt["id"])),
            )

        cur.execute(
            """
            UPDATE llm_benchmark_runs
            SET fail_reason=%s
            WHERE prompt_set_id=%s
              AND status IN ('PASS', 'FAILED')
            """,
            (CE_PROMPTS_INVALIDATED_FAIL_REASON, int(ps["id"])),
        )
        db.commit()
        return jsonify({"status": "success", "message": "CE benchmark questions saved. Existing CE benchmark results are now stale and should be rerun."})
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        return jsonify({"status": "error", "message": f"Could not save CE benchmark prompts: {e}"}), 500
    finally:
        try:
            cur.close()
            db.close()
        except Exception:
            pass

@benchmark_routes.route("/run", methods=["POST"])
@login_required(role="admin")
def bench_run():
    data = request.get_json() or {}
    profile = (data.get("profile") or CE_BENCHMARK_PROFILE_NAME).strip()
    promptset = (data.get("promptset") or CE_BENCHMARK_PROMPTSET_NAME).strip()
    version = (data.get("version") or CE_BENCHMARK_PROMPTSET_VERSION).strip()
    force = bool(data.get("force") or False)

    with _bench_lock:
        if _bench_state["running"]:
            return jsonify({"status": "error", "message": "Benchmarks already running."}), 400

        _bench_cancel.clear()
        _bench_state["running"] = True
        _bench_state["status"] = "running"
        _bench_state["message"] = "Starting benchmark thread..."
        _bench_state["started_at"] = int(time.time())
        _bench_state["ended_at"] = None

    admin_user_id = session.get("user_id")
    t = threading.Thread(
        target=benchmark_worker,
        args=(admin_user_id, profile, promptset, version, force),
        daemon=True,
    )
    t.start()

    return jsonify({"status": "success", "message": "Benchmarks started."})

@benchmark_routes.route("/stop", methods=["POST"])
@login_required(role="admin")
def bench_stop():
    with _bench_lock:
        if not _bench_state.get("running"):
            return jsonify({"status": "success", "message": "No benchmark running."})

        _bench_state["status"] = "stopping"
        _bench_state["message"] = "Stop requested. Cancelling…"

    _bench_cancel.set()

    try:
        stop_any_running_models()
    except Exception:
        pass

    try:
        admin_user_id = session.get("user_id")
        _emit_to_user(admin_user_id, "benchmark_progress", dict(_bench_state))
    except Exception:
        pass

    print("[BENCH] Stop requested via UI.")
    return jsonify({"status": "success", "message": "Stop requested."})

@benchmark_routes.route("/best", methods=["GET"])
@login_required(role="admin")
def bench_best():
    db = mysql_conn()
    cur = db.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT
              m.id AS model_id,
              m.model_name,
              m.model_path,
              m.file_size,
              m.fingerprint,
              m.allow_benchmark,

              best.run_id,
              best.status,
              best.started_at,
              best.ended_at,

              best.avg_eval_tps,
              best.min_eval_tps,
              best.max_eval_tps,
              best.avg_ttft_ms,
              best.total_tokens_generated,
              best.total_ms,

              best.fail_reason
            FROM llm_benchmark_models m
            JOIN (
              SELECT x.*
              FROM (
                SELECT
                  r.model_id,
                  r.id AS run_id,
                  CASE
                    WHEN COALESCE(r.fail_reason, '') = %s THEN 'STALE'
                    ELSE r.status
                  END AS status,
                  r.fail_reason,
                  r.started_at,
                  r.ended_at,

                  AVG(res.eval_tps) AS avg_eval_tps,
                  MIN(res.eval_tps) AS min_eval_tps,
                  MAX(res.eval_tps) AS max_eval_tps,
                  AVG(res.ttft_ms) AS avg_ttft_ms,
                  SUM(res.tokens_generated) AS total_tokens_generated,
                  SUM(res.total_ms) AS total_ms,

                  ROW_NUMBER() OVER (
                    PARTITION BY r.model_id
                    ORDER BY
                      (COALESCE(r.fail_reason, '') <> %s AND r.status='PASS') DESC,
                      CASE WHEN COALESCE(r.fail_reason, '') <> %s AND r.status='PASS' THEN AVG(res.eval_tps) ELSE -1 END DESC,
                      CASE WHEN COALESCE(r.fail_reason, '') <> %s AND r.status='PASS' THEN AVG(res.ttft_ms) ELSE 999999999 END ASC,
                      COALESCE(r.ended_at, r.started_at) DESC
                  ) AS rn
                FROM llm_benchmark_runs r
                LEFT JOIN llm_benchmark_results res ON res.run_id = r.id
                GROUP BY r.id
              ) x
              WHERE x.rn = 1
            ) best ON best.model_id = m.id
            ORDER BY best.avg_eval_tps DESC
            """,
            (
                CE_PROMPTS_INVALIDATED_FAIL_REASON,
                CE_PROMPTS_INVALIDATED_FAIL_REASON,
                CE_PROMPTS_INVALIDATED_FAIL_REASON,
                CE_PROMPTS_INVALIDATED_FAIL_REASON,
            )
        )
        rows = cur.fetchall() or []

        # Present-on-disk detection (unchanged)
        def _norm(s: str) -> str:
            if s is None:
                return ""
            s = str(s).strip().replace("\\", "/").lower()
            return s

        def _base(s: str) -> str:
            s = _norm(s)
            if not s:
                return ""
            parts = [p for p in s.split("/") if p]
            return parts[-1] if parts else s

        present_keys = set()
        present_basenames = set()
        try:
            discovered = find_models(model_routes.get_base_model_folder()) or []

            for m in discovered:
                if isinstance(m, dict):
                    name = m.get("value") or m.get("model_path") or m.get("path") or m.get("file") or m.get("model_name") or m.get("name") or ""
                else:
                    name = str(m)

                k = _norm(name)
                if k:
                    present_keys.add(k)
                    present_basenames.add(_base(k))
        except Exception:
            present_keys = set()
            present_basenames = set()

        for r in rows:
            try:
                r["size_gb"] = round((r.get("file_size") or 0) / (1024 ** 3), 2)
            except Exception:
                r["size_gb"] = 0

            try:
                mp = r.get("model_path") or ""
                mn = r.get("model_name") or ""
                mk = _norm(mp or mn)
                mb = _base(mp or mn)

                is_present = False
                if mk and (mk in present_keys):
                    is_present = True
                elif mb and (mb in present_basenames):
                    is_present = True
                elif mk and present_keys:
                    for pk in present_keys:
                        if mk in pk or pk in mk:
                            is_present = True
                            break

                r["present"] = bool(is_present)
            except Exception:
                r["present"] = False

        return jsonify({"status": "success", "rows": rows})
    finally:
        try:
            cur.close()
            db.close()
        except Exception:
            pass

@benchmark_routes.route("/run/<int:run_id>", methods=["GET"])
@login_required(role="admin")
def bench_run_details(run_id):
    db = mysql_conn()
    cur = db.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT r.*, m.model_name, m.model_path, p.name AS profile_name, ps.name AS promptset_name, ps.version AS promptset_version
            FROM llm_benchmark_runs r
            JOIN llm_benchmark_models m ON m.id = r.model_id
            JOIN llm_benchmark_profiles p ON p.id = r.profile_id
            JOIN llm_benchmark_prompt_sets ps ON ps.id = r.prompt_set_id
            WHERE r.id=%s
            LIMIT 1
            """,
            (int(run_id),),
        )
        run = cur.fetchone()
        if not run:
            return jsonify({"status": "error", "message": "Run not found"}), 404

        if (run.get("fail_reason") or "") == CE_PROMPTS_INVALIDATED_FAIL_REASON:
            run["status"] = "STALE"

        cur.execute(
            """
            SELECT
              res.*,
              pr.ordering,
              pr.category,
              pr.prompt_text,
              pr.max_tokens
            FROM llm_benchmark_results res
            JOIN llm_benchmark_prompts pr ON pr.id = res.prompt_id
            WHERE res.run_id=%s
            ORDER BY pr.ordering ASC
            """,
            (int(run_id),),
        )
        results = cur.fetchall() or []
        return jsonify({"status": "success", "run": run, "results": results})
    finally:
        try:
            cur.close()
            db.close()
        except Exception:
            pass
