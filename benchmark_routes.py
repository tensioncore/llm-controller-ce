from flask import Blueprint, Response, request, jsonify, session
import os
import time
import json
import threading
import datetime
import traceback
import csv
import io
import math
import uuid

import requests

from db_mysql import mysql_conn
from auth import login_required
from extensions import socketio
from helpers import find_models

import model_routes  # reuse llama-server launch helpers + constants

benchmark_routes = Blueprint("benchmark_routes", __name__)

_bench_lock = threading.Lock()

# Cancel flag. Threads cannot be killed safely; use a cooperative stop.
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
CE_CORE_SUITE_KEY = "ce.core"
BENCHMARK_CATEGORIES = (
    "knowledge", "coding", "practical_reasoning", "troubleshooting",
    "explanation_quality", "planning", "uncertainty_handling",
    "tool_calling", "writing_summarization", "performance_baseline",
)
CE_PROMPTS_INVALIDATED_FAIL_REASON = "ce_prompts_updated"

def _emit_to_user(user_id, event, payload):
    # Chat sockets use user_{user_id} rooms.
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

# Reset ALWAYS clears ALL benchmark runs for a model.
# There is intentionally NO failed-only mode.
@benchmark_routes.route("/reset_model", methods=["POST"])
@login_required(role="admin")
def bench_reset_model():
    data = request.get_json() or {}
    fingerprint = (data.get("fingerprint") or "").strip().lower()

    with _bench_lock:
        if _bench_state.get("running"):
            return jsonify({"status": "error", "message": "Benchmarks are running. Stop them first."}), 400

    if not fingerprint or len(fingerprint) != 40:
        return jsonify({"status": "error", "message": "Invalid fingerprint length (must be 40 hex chars)."}), 400
    try:
        int(fingerprint, 16)
    except Exception:
        return jsonify({"status": "error", "message": "Invalid fingerprint format (hex required)."}), 400

    db = mysql_conn()
    cur = db.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM llm_benchmark_models WHERE fingerprint=%s LIMIT 1", (fingerprint,))
        row = cur.fetchone()
        if not row or not row.get("id"):
            return jsonify({"status": "error", "message": "Fingerprint not found in registry."}), 404

        model_id = int(row["id"])

        # Always clear all runs. There is no failed-only mode.
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


def _get_registry_models_for_bench(include_disabled: bool = False):
    """
    Pull models ONLY from the registry table.
    - present on disk
    - enabled (unless include_disabled=True)
    - allow_benchmark=1
    - not marked as a projector file
    """
    db = mysql_conn()
    cur = db.cursor(dictionary=True)
    try:
        where = ["is_present=1", "allow_benchmark=1", "is_projector=0", "is_s2t=0", "LOWER(model_path) LIKE '%.gguf'"]
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

def _json_value(value, default=None):
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value


def _positive_id(value, label="ID"):
    if isinstance(value, bool) or not str(value).isdigit() or int(value) < 1:
        raise ValueError(f"{label} must be a positive integer.")
    return int(value)


class BenchmarkConflict(ValueError):
    pass


@benchmark_routes.errorhandler(Exception)
def benchmark_route_error(error):
    # Keep schema prerequisites visible without exposing connection configuration.
    if isinstance(error, BenchmarkConflict) or getattr(error, "errno", None) == 1062:
        message = str(error) if isinstance(error, BenchmarkConflict) else "That suite name/version or revision already exists. Reload and use a different name."
        return jsonify(status="error", message=message), 409
    if isinstance(error, ValueError):
        return jsonify(status="error", message=str(error)), 400
    if getattr(error, "errno", None) in (1054, 1146):
        return jsonify(status="error", message="Benchmarking v2 requires the operator-applied schema migration and suite seeds. See the benchmark documentation."), 503
    from werkzeug.exceptions import HTTPException
    if isinstance(error, HTTPException):
        return jsonify(status="error", message=error.description), error.code
    print(f"[BENCH] Route error: {error}")
    return jsonify(status="error", message="Benchmark operation failed. Check the CE console for details."), 500


def _read_suite(cur, suite_id=None):
    if suite_id is None:
        cur.execute("SELECT id, name, version, suite_key, revision, description, is_builtin, is_archived, created_at FROM llm_benchmark_prompt_sets WHERE suite_key=%s ORDER BY revision DESC LIMIT 1", (CE_CORE_SUITE_KEY,))
    else:
        cur.execute("SELECT id, name, version, suite_key, revision, description, is_builtin, is_archived, created_at FROM llm_benchmark_prompt_sets WHERE id=%s", (suite_id,))
    suite = cur.fetchone()
    if not suite:
        raise ValueError("Benchmark suite revision not found. Check the operator-applied suite seeds.")
    cur.execute("SELECT id, prompt_set_id, ordering, category, prompt_text, max_tokens, request_options_json FROM llm_benchmark_prompts WHERE prompt_set_id=%s ORDER BY ordering, id", (suite["id"],))
    suite["prompts"] = cur.fetchall() or []
    for prompt in suite["prompts"]:
        prompt["request_options_json"] = _json_value(prompt.get("request_options_json"))
    return suite


def get_suite(suite_id=None):
    db = mysql_conn()
    cur = db.cursor(dictionary=True)
    try:
        return _read_suite(cur, suite_id)
    finally:
        cur.close()
        db.close()


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

def create_run(model_id, profile_id, prompt_set_id, context):
    db = mysql_conn()
    cur = db.cursor()
    try:
        cur.execute(
            """
            INSERT INTO llm_benchmark_runs (model_id, profile_id, prompt_set_id, status, started_at, context_json)
            VALUES (%s, %s, %s, 'RUNNING', NOW(), %s)
            """,
            (model_id, profile_id, prompt_set_id, json.dumps(context)),
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
            WHERE id=%s AND status='RUNNING'
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

def update_run_context(run_id, context):
    db = mysql_conn()
    cur = db.cursor()
    try:
        cur.execute("UPDATE llm_benchmark_runs SET context_json=%s WHERE id=%s AND status='RUNNING'", (json.dumps(context), run_id))
        db.commit()
    finally:
        cur.close()
        db.close()


def insert_result(run_id, prompt_id, success, response_text, metrics, details, error=None):
    db = mysql_conn()
    cur = db.cursor()
    try:
        cur.execute(
            """
            INSERT INTO llm_benchmark_results
              (run_id, prompt_id, success, error, response_text,
               tokens_prompt, tokens_generated, ttft_ms,
               prompt_eval_tps, eval_tps, total_ms, details_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (int(run_id), int(prompt_id), 1 if success else 0,
             str(error)[:255] if error else None, response_text,
             metrics.get("tokens_prompt"), metrics.get("tokens_generated"), metrics.get("ttft_ms"),
             metrics.get("prompt_eval_tps"), metrics.get("eval_tps"), metrics["total_ms"], json.dumps(details)),
        )
        db.commit()
    finally:
        cur.close()
        db.close()


# We intentionally FORCE stop any running main/title model before starting.
def stop_any_running_models():
    try:
        model_routes._stop_main_model_runtime(clear_logs=True)
        model_routes._stop_title_model_runtime(clear_logs=True)
    except Exception:
        pass

def start_benchmark_model(model_path, settings, force_cpu_only=False):
    """
    Start benchmark models with the same split-GGUF and CPU-only normalization
    used by the main model start path.
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
        "extra_flags": extra_flags, "only_one_gpu": only_one_gpu,
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

def _validate_parameter_schema(schema, depth=0):
    # Deliberately bounded subset covering the supplied tool catalog. Unsupported
    # keywords are rejected rather than claiming validation we did not perform.
    allowed = {"type", "properties", "required", "additionalProperties", "items",
               "enum", "minimum", "maximum", "description", "title"}
    if depth > 12 or not isinstance(schema, dict) or set(schema) - allowed:
        raise ValueError("Tool parameter schema uses unsupported keywords or exceeds 12 levels.")
    kind = schema.get("type")
    if kind not in ("object", "array", "string", "integer", "number", "boolean", "null"):
        raise ValueError("Each tool parameter schema needs a supported explicit type.")
    for label in ("description", "title"):
        if label in schema and not isinstance(schema[label], str):
            raise ValueError(f"Schema {label} must be text.")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise ValueError("Schema enum must be a non-empty array.")
    for bound in ("minimum", "maximum"):
        if bound in schema and (kind not in ("integer", "number") or isinstance(schema[bound], bool)
                                or not isinstance(schema[bound], (int, float)) or (isinstance(schema[bound], float) and not math.isfinite(schema[bound]))):
            raise ValueError("Schema numeric bounds must be finite numbers on numeric fields.")
    if schema.get("minimum", -math.inf) > schema.get("maximum", math.inf):
        raise ValueError("Schema minimum exceeds maximum.")
    if kind == "object":
        props = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(props, dict) or not isinstance(required, list) or any(not isinstance(k, str) or k not in props for k in required):
            raise ValueError("Object schema properties/required are invalid.")
        if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], bool):
            raise ValueError("additionalProperties must be true or false.")
        for child in props.values():
            _validate_parameter_schema(child, depth + 1)
    elif set(schema) & {"properties", "required", "additionalProperties"}:
        raise ValueError("Object schema keywords require type object.")
    if kind == "array":
        _validate_parameter_schema(schema.get("items"), depth + 1)
    elif "items" in schema:
        raise ValueError("Schema items requires type array.")


def validate_request_options(options):
    if options is None:
        return {}
    if not isinstance(options, dict) or set(options) - {"tools", "tool_choice", "parallel_tool_calls"}:
        raise ValueError("Tool options allow only tools, tool_choice and parallel_tool_calls.")
    if not options:
        return {}
    if "tools" not in options:
        raise ValueError("Tool options require function definitions.")
    try:
        json.dumps(options, allow_nan=False)
    except (ValueError, TypeError):
        raise ValueError("Tool options must contain finite JSON values.")
    if not isinstance(options["tools"], list):
        raise ValueError("Tools must be an array of function definitions.")
    if any(not isinstance(tool, dict) or set(tool) - {"type", "function"} for tool in options["tools"]):
        raise ValueError("Each tool allows only type and function.")
    from api_routes import _controlled_tool_fields
    forwarded, size = _controlled_tool_fields(options)
    if size > 262144:
        raise ValueError("Tool definitions exceed 256 KiB.")
    names = set()
    for tool in forwarded["tools"]:
        function = tool["function"]
        if set(function) - {"name", "description", "parameters", "strict"}:
            raise ValueError("Unsupported function definition field.")
        if "strict" in function and not isinstance(function["strict"], bool):
            raise ValueError("Function strict must be true or false.")
        if function["name"] in names:
            raise ValueError("Tool names must be unique.")
        names.add(function["name"])
        schema = function.get("parameters", {"type": "object", "properties": {}})
        _validate_parameter_schema(schema)
        if schema["type"] != "object":
            raise ValueError("Function parameters must be an object schema.")
    choice = forwarded.get("tool_choice")
    if isinstance(choice, dict) and choice["function"]["name"] not in names:
        raise ValueError("The selected function is not in tools.")
    return forwarded


def _argument_errors(value, schema, path="arguments", depth=0):
    if depth > 12:
        return [f"{path}: nesting exceeds validation limit"]
    kind = schema["type"]
    matches = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool) and (isinstance(value, int) or math.isfinite(value)),
        "boolean": isinstance(value, bool), "null": value is None,
    }
    if not matches[kind]:
        return [f"{path}: expected {kind}"]
    errors = []
    if "enum" in schema and not any(type(value) is type(v) and value == v for v in schema["enum"]):
        errors.append(f"{path}: value is outside enum")
    if kind in ("integer", "number"):
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            errors.append(f"{path}: value is outside numeric bounds")
    if kind == "object":
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: required")
        for key, item in value.items():
            if key in props:
                errors.extend(_argument_errors(item, props[key], f"{path}.{key}", depth + 1))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{key}: unexpected property")
            if len(errors) >= 50:
                break
    elif kind == "array":
        for i, item in enumerate(value):
            errors.extend(_argument_errors(item, schema["items"], f"{path}[{i}]", depth + 1))
            if len(errors) >= 50:
                break
    return errors[:50]


def _tool_validation(calls, options):
    definitions = {t["function"]["name"]: t["function"] for t in options.get("tools", [])}
    choice = options.get("tool_choice", "auto")
    if not calls:
        required = choice == "required" or isinstance(choice, dict)
        return {"status": "invalid" if required else "not_applicable",
                "errors": ["A tool call was required but none was returned."] if required else []}
    if not isinstance(calls, list):
        return {"status": "invalid", "errors": ["tool_calls is not an array."]}
    errors = []
    if choice == "none":
        errors.append("A tool call was returned when tool_choice was none.")
    if options.get("parallel_tool_calls") is False and len(calls) > 1:
        errors.append("Multiple calls returned with parallel_tool_calls disabled.")
    for i, call in enumerate(calls):
        fn = call.get("function") if isinstance(call, dict) else None
        if not isinstance(fn, dict) or call.get("type") != "function" or not isinstance(fn.get("name"), str) or fn["name"] not in definitions:
            errors.append(f"Call {i + 1}: unknown or malformed function.")
            continue
        if isinstance(choice, dict) and fn["name"] != choice["function"]["name"]:
            errors.append(f"Call {i + 1}: does not match the required function.")
        try:
            raw = fn.get("arguments")
            if not isinstance(raw, str):
                raise ValueError("arguments must be a JSON string")
            args = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON number")))
            schema = definitions[fn["name"]].get("parameters", {"type": "object", "properties": {}})
            errors.extend(_argument_errors(args, schema, f"Call {i + 1}"))
        except (ValueError, TypeError, RecursionError, OverflowError):
            errors.append(f"Call {i + 1}: invalid JSON arguments.")
        if len(errors) >= 50:
            break
    return {"status": "invalid" if errors else "valid", "errors": errors[:50]}


def _backend_number(value, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        if not math.isfinite(value) or value < 0 or (integer and (int(value) != value or value > 2147483647)):
            return None
        return int(value) if integer else float(value)
    except (ValueError, OverflowError):
        return None


def call_completion(prompt_text, max_tokens, timeout_sec, request_options=None):
    options = validate_request_options(request_options)
    payload = {"model": "llama", "messages": [{"role": "user", "content": prompt_text}],
               "stream": False, "max_tokens": int(max_tokens), **options}
    t0 = time.monotonic()
    response = requests.post(model_routes.get_llama_main_completion_url(), json=payload, timeout=int(timeout_sec))
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("Backend returned an invalid completion payload.")
    if body.get("error"):
        raise requests.exceptions.HTTPError("Backend returned an error payload.", response=response)
    choices = body.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") or {}
    if not isinstance(message, dict):
        raise ValueError("Backend returned an invalid completion message.")
    text = message.get("content")
    if text is None:
        text = choice.get("text", body.get("content", body.get("response", "")))
    if text is None:
        text = ""
    if not isinstance(text, str):
        raise ValueError("Backend returned non-text completion content.")
    reasoning = message.get("reasoning_content")
    calls = message.get("tool_calls")
    finish = choice.get("finish_reason")
    validation = _tool_validation(calls, options)
    if finish == "length":
        outcome = "token_limit"
    elif calls:
        outcome = "tool_call" if validation["status"] == "valid" else "unknown"
    elif text.strip():
        outcome = "completed" if finish == "stop" and validation["status"] != "invalid" else "unknown"
    elif reasoning:
        outcome = "reasoning_only"
    else:
        outcome = "empty_response"
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    timings = body.get("timings") if isinstance(body.get("timings"), dict) else {}
    metrics = {
        "tokens_prompt": _backend_number(usage.get("prompt_tokens"), True),
        "tokens_generated": _backend_number(usage.get("completion_tokens"), True),
        "ttft_ms": None,
        "prompt_eval_tps": _backend_number(timings.get("prompt_per_second")),
        "eval_tps": _backend_number(timings.get("predicted_per_second")),
        "total_ms": round((time.monotonic() - t0) * 1000),
    }
    sources = {"tokens_prompt": "usage.prompt_tokens", "tokens_generated": "usage.completion_tokens",
               "prompt_eval_tps": "timings.prompt_per_second", "eval_tps": "timings.predicted_per_second",
               "total_ms": "local.monotonic", "ttft_ms": None}
    sources = {k: v if metrics[k] is not None else None for k, v in sources.items()}
    details = {"schema_version": 1, "outcome": outcome, "finish_reason": finish,
               "reasoning_content": reasoning, "tool_calls": calls, "tool_validation": validation,
               "metric_sources": sources,
               "backend_usage_extra": {k: usage[k] for k in ("total_tokens", "prompt_tokens_details", "completion_tokens_details") if k in usage},
               "backend_timings_extra": {k: timings[k] for k in ("prompt_n", "predicted_n", "cache_n", "prompt_ms", "predicted_ms") if k in timings}}
    return text, metrics, details


def _request_error_details(error, cancelled=False):
    detail = {"type": type(error).__name__, "message": str(error)[:2000]}
    response = getattr(error, "response", None)
    if response is not None:
        detail["http_status"] = response.status_code
        try:
            backend = response.json().get("error")
            if isinstance(backend, dict):
                detail["backend"] = {key: str(backend[key])[:2000] for key in ("type", "code", "message") if key in backend}
        except (ValueError, AttributeError):
            pass
    return {"schema_version": 1, "outcome": "cancelled" if cancelled else "request_error",
            "finish_reason": None, "error_detail": detail, "metric_sources": {"total_ms": "local.monotonic"}}


def _set_state(**kwargs):
    for k, v in kwargs.items():
        _bench_state[k] = v

def _log_state_tick():
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
    - Updates cancellation state; the worker releases its busy flag after cleanup
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
            ended_at=int(time.time()),
            status="idle",
            message=f"Benchmarks cancelled{(' (' + where + ')') if where else ''}.",
        )

    print(f"[BENCH] ===== BENCHMARK CANCELLED ===== { _fmt_ts(_bench_state['ended_at']) }")
    return

def _save_launch_context(run_id, context, launch_info, cpu_fallback):
    runtime = model_routes._build_runtime_state(
        launch_info.get("capabilities"), launch_info["n_gpu_layers"],
        launch_info["n_threads"], launch_info["cpu_only"])
    # These defaults are the existing benchmark launch_llama_instance arguments.
    runtime.update(ctx_size=8192, n_parallel=1, fit="on", cpu_fallback=cpu_fallback,
                   extra_flags=launch_info["extra_flags"], only_one_gpu=launch_info["only_one_gpu"])
    context["runtime"] = runtime
    update_run_context(run_id, context)


def benchmark_worker(admin_user_id, prof, ps, force=False):

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
    print(f"[BENCH] Profile={prof['name']} Suite={ps['name']} Revision={ps['revision']} Force={force}")

    try:

        # Source of truth: registry only (present + enabled + benchmarkable + not a projector).
        ggufs = _get_registry_models_for_bench(include_disabled=False)

        prompts = ps["prompts"] or []
        if not prompts:
            raise RuntimeError("Prompt set has no prompts.")

        with _bench_lock:
            _set_state(
                total_models=len(ggufs),
                total_prompts=len(prompts),
                suite_id=ps["id"], profile_id=prof["id"],
                message=f"Loaded {len(ggufs)} models; suite {ps['name']} revision {ps['revision']} with {len(prompts)} prompts.",
            )
        _emit_to_user(admin_user_id, "benchmark_status", dict(_bench_state))
        _log_state_tick()

        settings = prof["settings"] or {}
        per_prompt_timeout = _coerce_profile_setting(settings, "per_prompt_timeout_sec", int, minimum=1)
        warmup_enabled = _coerce_profile_setting(settings, "warmup_enabled", bool)
        warmup_max_tokens = _coerce_profile_setting(settings, "warmup_max_tokens", int, minimum=1)

        # Global token cap.
        max_tokens_cap = _positive_id(settings.get("max_tokens_cap", 2500), "Profile token cap")

        stop_any_running_models()

        for idx, m in enumerate(ggufs, start=1):
            if _bench_cancel.is_set():
                return _cancel_and_exit(admin_user_id, run_id=_bench_state.get("current_run_id"), where="before model start")

            with _bench_lock:
                _set_state(current_run_id=None)
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

            # Registry is source of truth; don't fingerprint/upsert here.
            # Just make sure the file actually exists on disk.
            if not os.path.isfile(model_path):
                with _bench_lock:
                    _bench_state["failed_models"] += 1
                    show_name = rel_path or os.path.basename(model_path)
                    _bench_state["message"] = f"FAILED (missing file on disk): {show_name}"
                _emit_to_user(admin_user_id, "benchmark_progress", dict(_bench_state))
                continue

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

            context = {
                "schema_version": 1,
                "model": {k: m.get(k) for k in ("id", "model_name", "model_path", "file_size", "fingerprint")},
                "profile": {"id": prof["id"], "name": prof["name"], "settings": {
                    k: settings[k] for k in ("temperature", "top_k", "top_p", "seed", "repeat_penalty",
                                            "n_gpu_layers", "n_threads", "n_cpu_threads") if k in settings}},
                "limits": {"max_tokens_cap": max_tokens_cap, "per_prompt_timeout_sec": per_prompt_timeout,
                           "warmup_enabled": warmup_enabled, "warmup_max_tokens": warmup_max_tokens},
                "runtime": None,
            }
            run_id = create_run(model_id, prof["id"], ps["id"], context)
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
                _save_launch_context(run_id, context, launch_info, False)

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
                    _save_launch_context(run_id, context, launch_info, True)

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

                    request_started = time.monotonic()
                    try:
                        print(f"[BENCH]   Prompt {pidx}/{len(prompts)} (max_tokens={max_tokens})")
                        text, metrics, details = call_completion(
                            prompt_text, max_tokens, timeout_sec=per_prompt_timeout,
                            request_options=p.get("request_options_json"))
                    except Exception as error:
                        model_failed = True
                        cancelled = _bench_cancel.is_set()
                        fail_reason = "cancelled" if cancelled else ("prompt_timeout" if isinstance(error, requests.exceptions.Timeout) else "prompt_error")
                        fail_prompt_id = prompt_id
                        metrics = {"total_ms": round((time.monotonic() - request_started) * 1000)}
                        details = _request_error_details(error, cancelled)
                        error_message = details["error_detail"].get("backend", {}).get("message") or str(error)
                        if p.get("request_options_json"):
                            error_message = "Tool-call request failed: " + error_message
                        insert_result(run_id, prompt_id, False, "", metrics, details, error=error_message)
                        print(f"[BENCH]   {fail_reason} on prompt_id={prompt_id}: {error}")
                        break
                    # Persistence is outside the request exception handler: a failed
                    # INSERT must not be reclassified or retried as a model failure.
                    if _bench_cancel.is_set():
                        details["outcome"] = "cancelled"
                        model_failed, fail_reason, fail_prompt_id = True, "cancelled", prompt_id
                    insert_result(run_id, prompt_id, True, text, metrics, details)
                    _log_state_tick()
                    if model_failed:
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

        if _bench_cancel.is_set():
            return _cancel_and_exit(admin_user_id, where="after model run")
        with _bench_lock:
            _set_state(
                ended_at=int(time.time()),
                status="done",
                message="Benchmarks completed.",
                current_prompt_idx=_bench_state.get("total_prompts", 0),
            )

        print(f"[BENCH] ===== BENCHMARK DONE ===== { _fmt_ts(_bench_state['ended_at']) }")
        _log_state_tick()

    except Exception as e:
        try:
            active_run = _bench_state.get("current_run_id")
            if active_run:
                context["error_detail"] = {"type": type(e).__name__, "message": str(e)[:2000]}
                try:
                    update_run_context(active_run, context)
                finally:
                    finish_run(active_run, "FAILED", "cancelled" if _bench_cancel.is_set() else "worker_error")
        except Exception as persist_error:
            print(f"[BENCH] Could not finalize run: {persist_error}")
        with _bench_lock:
            _set_state(
                ended_at=int(time.time()),
                status="idle" if _bench_cancel.is_set() else "error",
                message="Benchmarks cancelled." if _bench_cancel.is_set() else f"Benchmark worker error: {str(e)}",
            )
        print(f"[BENCH] ===== BENCHMARK ERROR ===== {e}")
        print(traceback.format_exc())
        if not _bench_cancel.is_set():
            _emit_to_user(admin_user_id, "benchmark_error", {"error": str(e), "trace": traceback.format_exc()})
    finally:
        try:
            stop_any_running_models()
        except Exception:
            pass
        with _bench_lock:
            _set_state(running=False)
            final_state = dict(_bench_state)
        _emit_to_user(admin_user_id, "benchmark_done", final_state)

@benchmark_routes.route("/status", methods=["GET"])
@login_required(role="admin")
def bench_status():
    with _bench_lock:
        return jsonify({"status": "success", "state": dict(_bench_state)})


@benchmark_routes.route("/suites", methods=["GET"])
@login_required(role="admin")
def bench_suites():
    db = mysql_conn()
    cur = db.cursor(dictionary=True)
    try:
        cur.execute("""SELECT ps.id, ps.name, ps.version, ps.suite_key, ps.revision, ps.description, ps.is_builtin, ps.is_archived, ps.created_at, (SELECT COUNT(*) FROM llm_benchmark_prompts p WHERE p.prompt_set_id=ps.id) AS prompt_count
                       FROM llm_benchmark_prompt_sets ps ORDER BY ps.name, ps.revision DESC""")
        suites = cur.fetchall() or []
        cur.execute("SELECT id, name FROM llm_benchmark_profiles ORDER BY name")
        return jsonify(status="success", suites=suites, profiles=cur.fetchall() or [], categories=BENCHMARK_CATEGORIES)
    finally:
        cur.close()
        db.close()


@benchmark_routes.route("/suites/<int:suite_id>", methods=["GET"])
@login_required(role="admin")
def bench_suite_details(suite_id):
    return jsonify(status="success", suite=get_suite(suite_id))


def _suite_payload(data):
    name = data.get("name")
    description = data.get("description", "")
    prompts = data.get("prompts")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise ValueError("Suite name must contain 1 to 120 characters.")
    if not isinstance(description, str) or len(description.encode("utf-8")) > 65535:
        raise ValueError("Suite description is too long.")
    if not isinstance(prompts, list) or not 1 <= len(prompts) <= 100:
        raise ValueError("A suite must contain 1 to 100 ordered prompts.")
    cleaned = []
    for i, prompt in enumerate(prompts, 1):
        if not isinstance(prompt, dict):
            raise ValueError(f"Prompt {i} is invalid.")
        text = prompt.get("prompt_text")
        if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 65535:
            raise ValueError(f"Prompt {i} must contain text within 65,535 UTF-8 bytes.")
        category = prompt.get("category")
        if category not in BENCHMARK_CATEGORIES:
            raise ValueError(f"Prompt {i} has an invalid category.")
        limit = _positive_id(prompt.get("max_tokens"), "Token limit")
        if limit > 32768:
            raise ValueError("Prompt token limits may not exceed 32,768.")
        options = validate_request_options(prompt.get("request_options_json"))
        cleaned.append((i, category, text, limit, json.dumps(options) if options else None))
    return name.strip(), description, cleaned


def _locked_family(cur, source_id):
    cur.execute("SELECT suite_key FROM llm_benchmark_prompt_sets WHERE id=%s", (source_id,))
    source = cur.fetchone()
    if not source:
        raise ValueError("Suite revision not found.")
    # Serialize revision creation across requests using an existing immutable root row.
    cur.execute("SELECT id FROM llm_benchmark_prompt_sets WHERE suite_key=%s ORDER BY revision LIMIT 1 FOR UPDATE", (source["suite_key"],))
    cur.fetchone()
    cur.execute("SELECT * FROM llm_benchmark_prompt_sets WHERE suite_key=%s ORDER BY revision DESC LIMIT 1 FOR UPDATE", (source["suite_key"],))
    return cur.fetchone()


@benchmark_routes.route("/suites", methods=["POST"])
@login_required(role="admin")
def bench_save_suite():
    data = request.get_json() or {}
    if not isinstance(data, dict):
        raise ValueError("Expected a suite object.")
    mode = data.get("mode", "create")
    if mode not in ("create", "edit", "duplicate"):
        raise ValueError("Invalid suite operation.")
    name, description, prompts = _suite_payload(data)
    with _bench_lock:
        if _bench_state["running"]:
            raise BenchmarkConflict("Stop benchmarks before managing suites.")
        db = mysql_conn()
        cur = db.cursor(dictionary=True)
        try:
            key, revision, builtin = "custom." + str(uuid.uuid4()), 1, 0
            if mode in ("edit", "duplicate"):
                source_id = _positive_id(data.get("source_id"), "Source revision")
                latest = _locked_family(cur, source_id)
                if mode == "edit":
                    if latest["id"] != source_id:
                        raise BenchmarkConflict("This suite has a newer revision. Reload it before editing.")
                    if latest["is_archived"]:
                        raise BenchmarkConflict("This suite is archived. Duplicate it to create an active suite.")
                    if latest["is_builtin"] and latest["suite_key"] != CE_CORE_SUITE_KEY:
                        raise ValueError("Duplicate a built-in suite before editing it.")
                    key, revision, builtin = latest["suite_key"], int(latest["revision"]) + 1, latest["is_builtin"]
            version = f"v{revision}"
            cur.execute("SELECT id FROM llm_benchmark_prompt_sets WHERE name=%s AND version=%s", (name, version))
            if cur.fetchone():
                raise BenchmarkConflict("That suite name/version already exists. Choose a different name.")
            cur.execute("""INSERT INTO llm_benchmark_prompt_sets
                           (name, version, suite_key, revision, description, is_builtin, is_archived)
                           VALUES (%s,%s,%s,%s,%s,%s,0)""", (name, version, key, revision, description, builtin))
            suite_id = cur.lastrowid
            cur.executemany("""INSERT INTO llm_benchmark_prompts
                               (prompt_set_id, ordering, category, prompt_text, max_tokens, request_options_json)
                               VALUES (%s,%s,%s,%s,%s,%s)""", [(suite_id, *p) for p in prompts])
            db.commit()
            return jsonify(status="success", suite_id=suite_id, message="Suite saved as a new revision. Existing runs are unchanged.")
        except Exception:
            db.rollback()
            raise
        finally:
            cur.close()
            db.close()


@benchmark_routes.route("/suites/<int:suite_id>/archive", methods=["POST"])
@login_required(role="admin")
def bench_archive_suite(suite_id):
    with _bench_lock:
        if _bench_state["running"]:
            raise BenchmarkConflict("Stop benchmarks before managing suites.")
        db = mysql_conn()
        cur = db.cursor(dictionary=True)
        try:
            latest = _locked_family(cur, suite_id)
            if latest["id"] != suite_id:
                raise BenchmarkConflict("This suite has a newer revision. Reload before archiving.")
            if latest["is_builtin"]:
                raise ValueError("Built-in suites cannot be archived.")
            cur.execute("UPDATE llm_benchmark_prompt_sets SET is_archived=1 WHERE suite_key=%s", (latest["suite_key"],))
            db.commit()
            return jsonify(status="success", message="Suite archived. Historical runs remain available.")
        except Exception:
            db.rollback()
            raise
        finally:
            cur.close()
            db.close()


@benchmark_routes.route("/run", methods=["POST"])
@login_required(role="admin")
def bench_run():
    data = request.get_json() or {}
    if not isinstance(data, dict):
        raise ValueError("Expected benchmark configuration.")
    if not isinstance(data.get("force", False), bool):
        raise ValueError("Force re-run must be true or false.")
    with _bench_lock:
        if _bench_state["running"]:
            raise BenchmarkConflict("Benchmarks already running.")
        suite_id = _positive_id(data["suite_id"], "Suite revision") if data.get("suite_id") is not None else None
        ps = get_suite(suite_id)
        if ps["is_archived"]:
            raise ValueError("Archived suites cannot be run. Duplicate the suite first.")
        if not ps["prompts"]:
            raise ValueError("The selected suite has no prompts. Check the operator-applied seeds.")
        for prompt in ps["prompts"]:
            validate_request_options(prompt.get("request_options_json"))
        prof = get_profile(data.get("profile") or CE_BENCHMARK_PROFILE_NAME)
        if not prof:
            raise ValueError("Benchmark profile not found.")
        _bench_cancel.clear()
        _set_state(running=True, status="running", message="Starting benchmark thread...",
                   started_at=int(time.time()), ended_at=None, current_run_id=None,
                   suite_id=ps["id"], profile_id=prof["id"])
        thread = threading.Thread(target=benchmark_worker,
                                  args=(session.get("user_id"), prof, ps, data.get("force", False)), daemon=True)
        try:
            thread.start()
        except Exception:
            _set_state(running=False, status="error", message="Could not start benchmark worker.")
            raise
    return jsonify(status="success", message="Benchmarks started.")


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
    suite = get_suite(_positive_id(request.args["suite_id"], "Suite revision") if request.args.get("suite_id") else None)
    profile = get_profile(request.args.get("profile") or CE_BENCHMARK_PROFILE_NAME)
    if not profile:
        raise ValueError("Benchmark profile not found.")
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

              best.fail_reason, best.legacy_metrics, best.completed_answers, best.result_count
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

                  AVG(CASE WHEN res.details_json IS NOT NULL THEN res.eval_tps END) AS avg_eval_tps,
                  MIN(CASE WHEN res.details_json IS NOT NULL THEN res.eval_tps END) AS min_eval_tps,
                  MAX(CASE WHEN res.details_json IS NOT NULL THEN res.eval_tps END) AS max_eval_tps,
                  AVG(CASE WHEN res.details_json IS NOT NULL THEN res.ttft_ms END) AS avg_ttft_ms,
                  (r.context_json IS NULL) AS legacy_metrics,
                  COUNT(res.id) AS result_count,
                  SUM(CASE WHEN JSON_UNQUOTE(JSON_EXTRACT(res.details_json, '$.outcome')) IN ('completed','tool_call') THEN 1 ELSE 0 END) AS completed_answers,
                  CASE WHEN COUNT(res.tokens_generated)=COUNT(res.id) THEN SUM(res.tokens_generated) END AS total_tokens_generated,
                  SUM(res.total_ms) AS total_ms,

                  ROW_NUMBER() OVER (
                    PARTITION BY r.model_id
                    ORDER BY
                      (COALESCE(r.fail_reason, '') <> %s AND r.status='PASS') DESC,
                      CASE WHEN COALESCE(r.fail_reason, '') <> %s AND r.status='PASS' THEN AVG(CASE WHEN res.details_json IS NOT NULL THEN res.eval_tps END) ELSE -1 END DESC,
                      COALESCE(r.ended_at, r.started_at) DESC
                  ) AS rn
                FROM llm_benchmark_runs r
                LEFT JOIN llm_benchmark_results res ON res.run_id = r.id
                WHERE r.prompt_set_id=%s AND r.profile_id=%s
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
                suite["id"], profile["id"],
            )
        )
        rows = cur.fetchall() or []

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
            r["total_prompts"] = len(suite["prompts"])
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

def _load_run(run_id):
    db = mysql_conn()
    cur = db.cursor(dictionary=True)
    try:
        cur.execute("""SELECT r.*, m.model_name, m.model_path, m.fingerprint, m.file_size,
                       p.name AS profile_name, ps.name AS promptset_name, ps.version AS promptset_version,
                       ps.suite_key, ps.revision, ps.description AS suite_description, ps.is_archived
                       FROM llm_benchmark_runs r
                       JOIN llm_benchmark_models m ON m.id=r.model_id
                       JOIN llm_benchmark_profiles p ON p.id=r.profile_id
                       JOIN llm_benchmark_prompt_sets ps ON ps.id=r.prompt_set_id WHERE r.id=%s""", (run_id,))
        run = cur.fetchone()
        if not run:
            from werkzeug.exceptions import NotFound
            raise NotFound("Model run not found.")
        run["context_json"] = _json_value(run.get("context_json"))
        run["legacy"] = run["context_json"] is None
        run["stale"] = run.get("fail_reason") == CE_PROMPTS_INVALIDATED_FAIL_REASON
        if run["context_json"]:
            for key in ("model_name", "model_path", "fingerprint", "file_size"):
                run[key] = run["context_json"].get("model", {}).get(key)
            run["profile_name"] = run["context_json"].get("profile", {}).get("name", run["profile_name"])
        cur.execute("""SELECT res.*, pr.id AS prompt_id, pr.ordering, pr.category, pr.prompt_text,
                       pr.max_tokens, pr.request_options_json
                       FROM llm_benchmark_prompts pr
                       LEFT JOIN llm_benchmark_results res ON res.prompt_id=pr.id AND res.run_id=%s
                       WHERE pr.prompt_set_id=%s ORDER BY pr.ordering, pr.id, res.id""", (run_id, run["prompt_set_id"]))
        results = cur.fetchall() or []
        for row in results:
            row["details_json"] = _json_value(row.get("details_json"))
            row["request_options_json"] = _json_value(row.get("request_options_json"))
            row["recorded"] = row.get("id") is not None
            row["legacy"] = row["recorded"] and row["details_json"] is None
            row["outcome"] = (row["details_json"] or {}).get("outcome", "unknown" if row["recorded"] else "not_recorded")
            cap = (run["context_json"] or {}).get("limits", {}).get("max_tokens_cap")
            row["effective_max_tokens"] = min(row["max_tokens"], cap) if cap is not None else None
        return {"export_version": 1, "scope": "model_run", "run": run, "results": results,
                "legacy_notice": "Legacy runs lack frozen runtime settings and may reference prompts edited before migration. Historical token counts may be estimates; Eval TPS used total request time. Stored values are unchanged." if run["legacy"] or any(r["legacy"] for r in results) else None}
    finally:
        cur.close()
        db.close()


def _iso_values(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _iso_values(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_iso_values(v) for v in value]
    return value


@benchmark_routes.route("/run/<int:run_id>", methods=["GET"])
@login_required(role="admin")
def bench_run_details(run_id):
    return jsonify(status="success", **_iso_values(_load_run(run_id)))


@benchmark_routes.route("/runs", methods=["GET"])
@login_required(role="admin")
def bench_history():
    suite = get_suite(_positive_id(request.args["suite_id"], "Suite revision") if request.args.get("suite_id") else None)
    profile = get_profile(request.args.get("profile") or CE_BENCHMARK_PROFILE_NAME)
    if not profile:
        raise ValueError("Benchmark profile not found.")
    before = _positive_id(request.args["before"], "History cursor") if request.args.get("before") else None
    db = mysql_conn()
    cur = db.cursor(dictionary=True)
    try:
        cur.execute("""SELECT r.id, r.model_id, m.model_name, r.status, r.fail_reason,
                       r.started_at, r.ended_at, r.context_json
                       FROM llm_benchmark_runs r JOIN llm_benchmark_models m ON m.id=r.model_id
                       WHERE r.prompt_set_id=%s AND r.profile_id=%s AND (%s IS NULL OR r.id < %s)
                       ORDER BY r.id DESC LIMIT 101""", (suite["id"], profile["id"], before, before))
        rows = cur.fetchall() or []
        has_more = len(rows) > 100
        rows = rows[:100]
        for row in rows:
            context = _json_value(row.pop("context_json"))
            row["legacy"] = context is None
            if context:
                row["model_name"] = context.get("model", {}).get("model_name", row["model_name"])
        return jsonify(status="success", rows=_iso_values(rows), next_before=rows[-1]["id"] if has_more else None)
    finally:
        cur.close()
        db.close()


def _csv_cell(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value.lstrip(" \t\r\n\ufeff").startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


@benchmark_routes.route("/run/<int:run_id>/export", methods=["GET"])
@benchmark_routes.route("/run/<int:run_id>/result/<int:result_id>/export", methods=["GET"])
@login_required(role="admin")
def bench_export(run_id, result_id=None):
    fmt = request.args.get("format", "json")
    if fmt not in ("json", "csv"):
        raise ValueError("Choose JSON or CSV export.")
    data = _iso_values(_load_run(run_id))
    if result_id is not None:
        data["results"] = [r for r in data["results"] if r.get("id") == result_id]
        if not data["results"]:
            from werkzeug.exceptions import NotFound
            raise NotFound("Result does not belong to this model run.")
        data["scope"] = "result"
    data["exported_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if fmt == "json":
        response = jsonify(data)
    else:
        run = data["run"]
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        columns = ["export_version", "run_id", "result_id", "model_id", "model_name", "model_path", "fingerprint", "file_size",
                   "suite_key", "suite_name", "revision", "suite_version", "suite_description", "profile_name", "run_status", "run_fail_reason",
                   "started_at", "ended_at", "context_json", "prompt_id", "ordering", "category", "prompt_text",
                   "max_tokens", "effective_max_tokens", "request_options_json", "recorded", "success", "outcome",
                   "tokens_prompt", "tokens_generated", "ttft_ms", "prompt_eval_tps", "eval_tps", "total_ms",
                   "response_text", "finish_reason", "reasoning_content", "tool_calls", "tool_validation",
                   "metric_sources", "error", "details_json", "legacy_notice"]
        writer.writerow(columns)
        for row in data["results"]:
            values = {**row, "export_version": data["export_version"], "run_id": run_id, "result_id": row.get("id"),
                      **{k: run.get(k) for k in ("model_id", "model_name", "model_path", "fingerprint", "file_size", "suite_key", "revision", "profile_name", "started_at", "ended_at", "context_json")},
                      "suite_name": run["promptset_name"], "suite_version": run["promptset_version"], "suite_description": run["suite_description"],
                      "run_status": run["status"], "run_fail_reason": run["fail_reason"], "legacy_notice": data["legacy_notice"]}
            details = row.get("details_json") or {}
            for key in ("finish_reason", "reasoning_content", "tool_calls", "tool_validation", "metric_sources"):
                values[key] = details.get(key)
            writer.writerow([_csv_cell(values.get(k)) for k in columns])
        response = Response(output.getvalue(), mimetype="text/csv")
    suffix = f"-result-{result_id}" if result_id is not None else ""
    response.headers["Content-Disposition"] = f'attachment; filename="benchmark-run-{run_id}{suffix}.{fmt}"'
    response.headers["Cache-Control"] = "no-store"
    return response
