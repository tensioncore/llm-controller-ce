from flask import Blueprint, request, jsonify
import sqlite3
import os
import json
import shutil
import subprocess
import requests
import threading
import hashlib
import time
import datetime as dt
import re

from helpers import DB_PATH
from app_settings import get_setting, set_setting
from db_mysql import mysql_conn
from runtime_config import (
    get_llama_main_port,
    get_llama_title_port,
    resolve_scan_directory_path,
)
from settings_routes import get_db
from auth import login_required

model_routes = Blueprint('model_routes', __name__)

main_process = None
title_process = None
title_ready_process = None
title_runtime_failure_model = ""
main_log_buffer = []
title_log_buffer = []
current_main_model_used = ""
current_title_model_used = ""
current_main_model_settings = {}
current_title_model_settings = {}
current_main_model_runtime = {}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SPLIT_GGUF_PATTERN = re.compile(r"^(.*)-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)
GPU_OOM_LOG_PATTERNS = (
    "cudamalloc failed",
    "failed to allocate cuda buffer",
    "unable to allocate cuda0 buffer",
    "unable to allocate cuda buffer",
    "cuda error out of memory",
    "cuda out of memory",
    "hipmalloc failed",
    "failed to allocate hip buffer",
    "hip error out of memory",
    "rocm error out of memory",
)
MODEL_LOAD_FAILURE_PATTERNS = GPU_OOM_LOG_PATTERNS + (
    "failed to load model",
    "exiting due to model loading error",
    "model loading error",
)
TITLE_MODEL_SETTING_KEY = "llm.title_model_path"
MODEL_PROFILE_FIELDS = (
    "profile_general",
    "profile_coding",
    "profile_writing",
    "profile_reasoning",
    "profile_math",
    "profile_agents",
    "profile_images",
)


def get_base_model_folder():
    return resolve_scan_directory_path()


def resolve_model_path(model_path: str) -> str:
    path = str(model_path or "").strip().replace("\\", "/")
    if not path:
        return ""
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(get_base_model_folder(), path))


def get_llama_main_completion_url() -> str:
    return f"http://127.0.0.1:{get_llama_main_port()}/v1/chat/completions"


def get_llama_title_completion_url() -> str:
    return f"http://127.0.0.1:{get_llama_title_port()}/v1/chat/completions"


def _mysql_conn():
    conn = get_db()
    if conn is None:
        raise RuntimeError("MySQL connection not available (get_db returned None)")
    try:
        conn.autocommit = True
    except Exception:
        pass
    return conn


def _now_dt():
    return dt.datetime.now()


def _get_db_default_int(key, minimum=None, maximum=None):
    try:
        value = int(get_setting(key, cast=int))
    except Exception as e:
        raise RuntimeError(f"Missing required setting: {key}") from e

    if minimum is not None and value < minimum:
        raise RuntimeError(f"Invalid required setting: {key}")
    if maximum is not None and value > maximum:
        raise RuntimeError(f"Invalid required setting: {key}")
    return value


def _get_db_default_float(key, minimum=None, maximum=None):
    try:
        value = float(get_setting(key, cast=float))
    except Exception as e:
        raise RuntimeError(f"Missing required setting: {key}") from e

    if minimum is not None and value < minimum:
        raise RuntimeError(f"Invalid required setting: {key}")
    if maximum is not None and value > maximum:
        raise RuntimeError(f"Invalid required setting: {key}")
    return value


def _get_llama_server_path():
    try:
        llama_server_setting = str(get_setting("llm.llama_server_path") or "").strip()
    except Exception as e:
        raise RuntimeError("Unable to load required setting: llm.llama_server_path") from e

    if not llama_server_setting:
        raise RuntimeError("Missing required setting: llm.llama_server_path")

    if os.path.isabs(llama_server_setting):
        return os.path.normpath(llama_server_setting)
    return os.path.normpath(os.path.join(BASE_DIR, llama_server_setting))


def _get_configured_llama_server_path_or_none():
    try:
        return _get_llama_server_path()
    except Exception:
        return None


def _get_dual_gpu_split_threshold():
    try:
        return _get_db_default_int("llama.dual_gpu_split_threshold_gb", minimum=0)
    except Exception as e:
        raise RuntimeError("Unable to load required setting: llama.dual_gpu_split_threshold_gb") from e


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()


def _as01(v) -> int:
    """Normalize DB-ish values to strict 0/1 for API output only."""
    try:
        if v is None:
            return 0
        if isinstance(v, bool):
            return 1 if v else 0
        if isinstance(v, int):
            return 1 if v == 1 else 0
        if isinstance(v, (bytes, bytearray)):
            return 1 if v in (b"1", b"\x01") else 0
        if isinstance(v, str):
            return 1 if v.strip() == "1" else 0
    except Exception:
        pass
    return 0


def _parse_split_gguf_filename(filename: str):
    match = SPLIT_GGUF_PATTERN.match(str(filename or "").strip())
    if not match:
        return None

    base_name = match.group(1)
    try:
        shard_index = int(match.group(2))
        shard_count = int(match.group(3))
    except Exception:
        return None

    if shard_index < 1 or shard_count < 1 or shard_index > shard_count:
        return None

    return {
        "base_name": base_name,
        "shard_index": shard_index,
        "shard_count": shard_count,
    }


def _build_split_gguf_part_paths(dir_path: str, base_name: str, shard_count: int):
    paths = []
    for index in range(1, shard_count + 1):
        filename = f"{base_name}-{index:05d}-of-{shard_count:05d}.gguf"
        paths.append(os.path.normpath(os.path.join(dir_path, filename)))
    return paths


def _resolve_split_gguf_info(model_path: str):
    if not model_path:
        return None

    full_path = os.path.normpath(model_path)
    parsed = _parse_split_gguf_filename(os.path.basename(full_path))
    if not parsed:
        return None

    dir_path = os.path.dirname(full_path)
    part_paths = _build_split_gguf_part_paths(dir_path, parsed["base_name"], parsed["shard_count"])
    missing_parts = [path for path in part_paths if not os.path.isfile(path)]

    return {
        "base_name": parsed["base_name"],
        "shard_index": parsed["shard_index"],
        "shard_count": parsed["shard_count"],
        "part_paths": part_paths,
        "first_path": part_paths[0] if part_paths else full_path,
        "is_complete": len(missing_parts) == 0,
        "missing_parts": missing_parts,
    }


def _normalize_model_launch_path(model_path: str):
    full_path = os.path.normpath(model_path)
    split_info = _resolve_split_gguf_info(full_path)
    if not split_info:
        return full_path, None, None

    if not split_info["is_complete"] or not os.path.isfile(split_info["first_path"]):
        return full_path, split_info, "Split GGUF model set is incomplete and cannot be launched."

    return split_info["first_path"], split_info, None


def _get_user_facing_model_name(path_or_name: str) -> str:
    if not path_or_name:
        return "Unknown"

    base = os.path.basename(str(path_or_name).strip())
    split_meta = _parse_split_gguf_filename(base)
    if split_meta:
        return split_meta["base_name"]

    noext = os.path.splitext(base)[0]
    return noext or base


def _get_model_title(path_or_name: str, friendly_name=None) -> str:
    friendly = str(friendly_name or "").strip()
    if friendly:
        return friendly

    base = os.path.basename(str(path_or_name or "").strip())
    if not base:
        return "Unknown"
    if base.lower().endswith(".gguf"):
        base = base[:-5]
    return base or "Unknown"


def _detect_model_load_failure(log_buffer):
    recent_lines = [str(line or "").strip() for line in (log_buffer or [])[-80:]]
    recent_lines = [line for line in recent_lines if line]
    if not recent_lines:
        return None

    lowered_lines = [line.lower() for line in recent_lines]

    for patterns, error_code, retry_cpu_only in (
        (GPU_OOM_LOG_PATTERNS, "gpu_oom", True),
        (MODEL_LOAD_FAILURE_PATTERNS, "load_failed", False),
    ):
        for line, lowered in zip(reversed(recent_lines), reversed(lowered_lines)):
            if any(pattern in lowered for pattern in patterns):
                return {
                    "message": line,
                    "error_code": error_code,
                    "retry_cpu_only": retry_cpu_only,
                }

    return None


def _tensor_split_gpu_count(extra_flags=None):
    flags = list(extra_flags or [])
    for index, flag in enumerate(flags):
        if flag == "--tensor-split" and index + 1 < len(flags):
            split_value = str(flags[index + 1] or "").strip()
            if not split_value:
                return None
            split_parts = [part.strip() for part in split_value.split(",") if part.strip()]
            return len(split_parts) if split_parts else None
    return None


def _build_tensor_split_vector(gpu_count):
    try:
        count = int(gpu_count or 0)
    except Exception:
        count = 0

    if count < 2:
        return ""
    return ",".join(["1"] * count)


def _format_runtime_summary(
    n_gpu_layers,
    *,
    cpu_only=False,
    only_one_gpu=True,
    extra_flags=None,
    mode=None,
    runtime_backend=None,
    gpu_vendor=None,
):
    try:
        effective_layers = int(n_gpu_layers or 0)
    except Exception:
        effective_layers = 0

    runtime_label = "CPU" if cpu_only or effective_layers == 0 else "GPU+CPU"
    parts = [
        f"runtime={runtime_label}",
        f"n_gpu_layers={effective_layers}",
    ]

    backend_value = str(runtime_backend or "").strip().lower()
    vendor_value = str(gpu_vendor or "").strip().lower()
    if backend_value:
        parts.append(f"backend={backend_value}")
    if vendor_value and vendor_value != "none":
        parts.append(f"vendor={vendor_value}")

    split_gpu_count = _tensor_split_gpu_count(extra_flags)
    if split_gpu_count and split_gpu_count > 1:
        parts.append(f"split={split_gpu_count}gpu")
    elif not cpu_only and not only_one_gpu:
        parts.append("split=multi-gpu")

    if mode:
        parts.append(f"mode={mode}")

    return " ".join(parts)


def _describe_launch_plan(launch_plan, *, mode=None):
    plan = launch_plan or {}
    capabilities = plan.get("capabilities") or {}
    cpu_only = bool(plan.get("cpu_only"))
    return _format_runtime_summary(
        int(plan.get("n_gpu_layers") or 0),
        cpu_only=cpu_only,
        only_one_gpu=bool(plan.get("only_one_gpu")),
        extra_flags=plan.get("extra_flags"),
        mode=mode,
        runtime_backend=("cpu" if cpu_only else capabilities.get("runtime_backend")),
        gpu_vendor=capabilities.get("gpu_vendor"),
    )


def _resolve_llama_server_executable():
    exe_path = _get_llama_server_path()
    if os.path.isfile(exe_path):
        return os.path.normpath(exe_path)

    found = shutil.which(os.path.basename(exe_path))
    if found:
        return os.path.normpath(found)

    raise FileNotFoundError(f"llama-server executable not found at {exe_path}")


def _scan_llms_files():
    rows = []
    base_model_folder = get_base_model_folder()
    if not os.path.isdir(base_model_folder):
        return rows

    split_groups = {}

    for root, dirs, files in os.walk(base_model_folder):
        for filename in files:
            if not filename.lower().endswith((".gguf", ".nemo")):
                continue

            full_path = os.path.normpath(os.path.join(root, filename))
            rel_path = os.path.normpath(os.path.relpath(full_path, base_model_folder)).replace("\\", "/")

            try:
                size_bytes = int(os.path.getsize(full_path))
            except Exception:
                size_bytes = 0

            try:
                mtime = int(os.path.getmtime(full_path))
            except Exception:
                mtime = 0

            split_meta = _parse_split_gguf_filename(filename)
            if split_meta:
                group_key = (
                    os.path.normcase(os.path.normpath(root)),
                    split_meta["base_name"].lower(),
                    split_meta["shard_count"],
                )
                group = split_groups.setdefault(group_key, {
                    "root": root,
                    "base_name": split_meta["base_name"],
                    "shard_count": split_meta["shard_count"],
                    "parts": {},
                })
                group["parts"][split_meta["shard_index"]] = {
                    "filename": filename,
                    "full_path": full_path,
                    "rel_path": rel_path,
                    "file_size": size_bytes,
                    "mtime": mtime,
                }
                continue

            fp = _sha1(rel_path.lower())

            rows.append({
                "fingerprint": fp,
                "model_name": os.path.splitext(os.path.basename(filename))[0],
                "model_path": rel_path,
                "file_size": size_bytes,
                "mtime": mtime,
                "rel_path": rel_path
            })

    for group in split_groups.values():
        shard_count = group["shard_count"]
        parts = group["parts"]
        if 1 not in parts or len(parts) != shard_count or any(index not in parts for index in range(1, shard_count + 1)):
            continue

        shard1 = parts[1]
        total_size = sum((part.get("file_size") or 0) for part in parts.values())
        latest_mtime = max((part.get("mtime") or 0) for part in parts.values())
        fp = _sha1(shard1["rel_path"].lower())

        rows.append({
            "fingerprint": fp,
            "model_name": group["base_name"],
            "model_path": shard1["rel_path"],
            "file_size": total_size,
            "mtime": latest_mtime,
            "rel_path": shard1["rel_path"]
        })

    rows.sort(key=lambda row: (str(row.get("model_name") or "").lower(), str(row.get("model_path") or "").lower()))
    return rows


def registry_rescan():
    scan_rows = _scan_llms_files()
    seen_fps = [r["fingerprint"] for r in scan_rows]
    seen_set = set(seen_fps)

    # This helper owns its connection lifetime and may be called from routes
    # that continue doing more DB work in the same request.
    conn = mysql_conn()
    cur = conn.cursor()

    now = _now_dt()

    upsert_sql = """
        INSERT INTO llm_benchmark_models
            (fingerprint, model_name, model_path, file_size, mtime, first_seen_at, last_seen_at, is_present)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, 1)
        ON DUPLICATE KEY UPDATE
            model_name    = VALUES(model_name),
            model_path    = VALUES(model_path),
            file_size     = VALUES(file_size),
            mtime         = VALUES(mtime),
            last_seen_at  = VALUES(last_seen_at),
            is_present    = 1
    """

    for r in scan_rows:
        cur.execute(upsert_sql, (
            r["fingerprint"],
            r["model_name"],
            r["model_path"],
            r["file_size"],
            r["mtime"],
            now,
            now
        ))

    if seen_fps:
        placeholders = ",".join(["%s"] * len(seen_fps))
        cur.execute(f"""
            UPDATE llm_benchmark_models
            SET is_present = 0,
                is_enabled = 0
            WHERE fingerprint NOT IN ({placeholders})
        """, tuple(seen_fps))
    else:
        cur.execute("""
            UPDATE llm_benchmark_models
            SET is_present = 0,
                is_enabled = 0
        """)

    conn.commit()
    cur.close()
    conn.close()

    _get_title_model_selection(clear_invalid=True)

    return {"scanned": len(scan_rows), "seen": len(seen_set)}


def registry_list(include_disabled=True):
    conn = _mysql_conn()
    cur = conn.cursor(dictionary=True)

    where = ""
    if not include_disabled:
        where = "WHERE is_enabled=1 AND is_projector=0 AND is_s2t=0 AND LOWER(model_path) LIKE '%.gguf'"

    sql = f"""
        SELECT
            id, fingerprint, model_name, friendly_name, model_path, file_size, mtime,
            first_seen_at, last_seen_at,
            is_enabled, is_favorite, allow_benchmark, is_projector, is_s2t, is_present,
            notes, profile_general, profile_coding, profile_writing,
            profile_reasoning, profile_math, profile_agents, profile_images,
            mmproj_path
        FROM llm_benchmark_models
        {where}
        ORDER BY is_favorite DESC, is_enabled DESC, model_name ASC
    """
    cur.execute(sql)
    rows = cur.fetchall() or []
    cur.close()
    conn.close()
    return rows


def _registry_path_key(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        resolved = resolve_model_path(raw)
    except Exception:
        resolved = raw
    return os.path.normcase(os.path.realpath(os.path.abspath(resolved)))


def _find_registry_model_by_path(model_path, enabled_present_only=False):
    target_key = _registry_path_key(model_path)
    if not target_key:
        return None

    conn = mysql_conn()
    cur = conn.cursor(dictionary=True)
    try:
        where = "WHERE is_enabled=1 AND is_present=1 AND is_projector=0 AND is_s2t=0 AND LOWER(model_path) LIKE '%.gguf'" if enabled_present_only else ""
        cur.execute(f"""
            SELECT
                id, fingerprint, model_name, friendly_name, model_path, file_size, mtime,
                is_enabled, is_favorite, allow_benchmark, is_projector, is_s2t, is_present,
                notes, profile_general, profile_coding, profile_writing,
                profile_reasoning, profile_math, profile_agents, profile_images,
                mmproj_path
            FROM llm_benchmark_models
            {where}
        """)
        for row in cur.fetchall() or []:
            if _registry_path_key(row.get("model_path")) == target_key:
                return row
        return None
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def _normalize_mmproj_path_for_storage(value):
    raw = str(value or "")
    if "\x00" in raw:
        raise ValueError("Projector path contains an invalid character.")
    raw = raw.strip()
    if not raw:
        return ""
    if "\r" in raw or "\n" in raw:
        raise ValueError("Projector path must be a single line.")
    if len(raw) > 1024:
        raise ValueError("Projector path must be 1024 characters or fewer.")

    base_path = os.path.realpath(os.path.abspath(get_base_model_folder()))
    candidate = raw if os.path.isabs(raw) else os.path.join(base_path, raw.replace("/", os.sep).replace("\\", os.sep))
    candidate_path = os.path.realpath(os.path.abspath(candidate))
    try:
        inside_scan_directory = os.path.commonpath(
            [os.path.normcase(base_path), os.path.normcase(candidate_path)]
        ) == os.path.normcase(base_path)
    except ValueError:
        inside_scan_directory = False
    if not inside_scan_directory:
        raise ValueError("Projector file must be inside the configured model scan directory.")
    if not os.path.isfile(candidate_path):
        raise ValueError("Projector file was not found.")

    stored = os.path.relpath(candidate_path, base_path).replace("\\", "/")
    if len(stored) > 1024:
        raise ValueError("Stored projector path must be 1024 characters or fewer.")
    return stored


def _resolve_configured_mmproj_path(value):
    stored = _normalize_mmproj_path_for_storage(value)
    return resolve_model_path(stored) if stored else ""


def _clear_title_model_selection():
    try:
        set_setting(TITLE_MODEL_SETTING_KEY, "", "string")
    except Exception as exc:
        print(f"[WARN] Could not clear invalid title-model selection: {exc}")


def _get_title_model_selection(clear_invalid=True):
    try:
        configured_path = str(get_setting(TITLE_MODEL_SETTING_KEY, default="") or "").strip()
    except Exception:
        return None
    if not configured_path:
        return None

    try:
        row = _find_registry_model_by_path(configured_path, enabled_present_only=True)
    except Exception:
        return None
    if row:
        resolved_path = resolve_model_path(row.get("model_path"))
        if os.path.isfile(resolved_path):
            row["resolved_path"] = os.path.normpath(resolved_path)
            return row

    if clear_invalid:
        _clear_title_model_selection()
    return None


def get_title_generation_completion_urls():
    urls = []
    selected = _get_title_model_selection(clear_invalid=True)
    if selected and _is_title_model_ready():
        selected_key = _registry_path_key(selected.get("resolved_path"))
        running_key = _registry_path_key(current_title_model_used)
        if selected_key and selected_key == running_key:
            urls.append(get_llama_title_completion_url())

    main_url = get_llama_main_completion_url()
    if main_url not in urls:
        urls.append(main_url)
    return urls


def _title_generation_status():
    selected = _get_title_model_selection(clear_invalid=True)
    title_running = bool(title_process and title_process.poll() is None)
    selected_key = _registry_path_key(selected.get("resolved_path")) if selected else ""
    running_key = _registry_path_key(current_title_model_used)
    selected_runtime_alive = bool(
        selected
        and title_running
        and selected_key == running_key
    )
    dedicated_running = bool(selected_runtime_alive and _is_title_model_ready())
    dedicated_failed = bool(
        selected_key
        and not selected_runtime_alive
        and (
            selected_key == _registry_path_key(title_runtime_failure_model)
            or (
                title_process is not None
                and title_process.poll() is not None
                and selected_key == running_key
            )
        )
    )
    return {
        "selected_model": selected.get("model_name") if selected else None,
        "selected_model_path": selected.get("model_path") if selected else "",
        "mode": "dedicated" if dedicated_running else "main_model",
        "dedicated_runtime": (
            "running" if dedicated_running else (
                "starting" if selected_runtime_alive else ("failed" if dedicated_failed else "stopped")
            )
        ),
        "fallback_active": not dedicated_running,
    }


def _basename_tps_key(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    base = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    split_meta = _parse_split_gguf_filename(base)
    if split_meta:
        return split_meta["base_name"].lower()
    if base.lower().endswith(".gguf"):
        base = base[:-5]
    return base.lower()


def _chat_tps_path_key(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    return os.path.normpath(raw).replace("\\", "/").lower()


def _chat_tps_name_key(value):
    return str(value or "").strip().lower()


def _looks_like_model_path(value):
    raw = str(value or "").strip()
    return bool(raw and ("/" in raw or "\\" in raw or raw.lower().endswith(".gguf")))


def _remember_chat_max_tps(bucket, keys, tps_value):
    for key in keys:
        if not key:
            continue
        previous = bucket.get(key)
        if previous is None or tps_value > previous:
            bucket[key] = tps_value


def _load_chat_max_tps_maps():
    maps = {"path": {}, "name": {}, "basename": {}}
    connection = None
    try:
        connection = sqlite3.connect(DB_PATH)
        cursor = connection.cursor()
        cursor.execute("""
            SELECT model_used, MAX(tps) AS max_tps
            FROM chats
            WHERE model_used IS NOT NULL AND model_used != ''
            GROUP BY model_used
        """)
        for model_used, max_tps in cursor.fetchall() or []:
            try:
                tps_value = float(max_tps) if max_tps is not None else None
            except Exception:
                tps_value = None
            if tps_value is None:
                continue

            raw = str(model_used or "").strip()
            if not raw:
                continue
            if _looks_like_model_path(raw):
                path_keys = [_chat_tps_path_key(raw)]
                if not os.path.isabs(raw):
                    try:
                        path_keys.append(_chat_tps_path_key(resolve_model_path(raw)))
                    except Exception:
                        pass
                _remember_chat_max_tps(maps["path"], path_keys, tps_value)
            else:
                _remember_chat_max_tps(maps["name"], [_chat_tps_name_key(raw)], tps_value)
            _remember_chat_max_tps(maps["basename"], [_basename_tps_key(raw)], tps_value)
    except Exception:
        pass
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
    return maps


def _lookup_chat_max_tps(name, path, maps):
    seen = set()
    candidates = [path]
    if path:
        try:
            candidates.append(resolve_model_path(path))
        except Exception:
            pass
    for candidate in candidates:
        key = _chat_tps_path_key(candidate)
        if key and key not in seen:
            seen.add(key)
            value = maps["path"].get(key)
            if value is not None:
                return value

    path_basename_key = _basename_tps_key(path)
    name_key = _chat_tps_name_key(name)
    if name_key and (not path_basename_key or name_key == path_basename_key):
        value = maps["name"].get(name_key)
        if value is not None:
            return value

    basename_keys = []
    if path_basename_key:
        basename_keys.append(path_basename_key)
    name_basename_key = _basename_tps_key(name)
    if name_basename_key and (not path_basename_key or name_basename_key == path_basename_key):
        basename_keys.append(name_basename_key)
    for key in basename_keys:
        value = maps["basename"].get(key)
        if value is not None:
            return value
    return None

def _infer_runtime_backend_from_executable_path(executable_path: str) -> str:
    path_text = str(executable_path or "").strip().lower()
    if not path_text:
        return "unknown"

    tokens = [token for token in re.split(r"[^a-z0-9]+", path_text) if token]
    if any(token in ("rocm", "hip") for token in tokens):
        return "rocm"
    if any(token in ("cuda", "nvidia") for token in tokens):
        return "cuda"
    if any(re.fullmatch(r"cu\d+", token) for token in tokens):
        return "cuda"
    return "unknown"


def get_gpu_backend_state(executable_path=None):
    nvidia_smi_found = shutil.which("nvidia-smi") is not None
    rocm_smi_found = shutil.which("rocm-smi") is not None
    rocminfo_found = shutil.which("rocminfo") is not None

    nvidia_gpu_count = _count_nvidia_gpus() if nvidia_smi_found else 0
    amd_rocm_smi_gpu_count = _count_amd_gpus_from_rocm_smi() if rocm_smi_found else 0
    amd_rocminfo_gpu_count = _count_amd_gpus_from_rocminfo() if rocminfo_found else 0
    amd_gpu_count = max(int(amd_rocm_smi_gpu_count), int(amd_rocminfo_gpu_count))

    gpu_vendor = "none"
    gpu_count = 0
    telemetry_backend = "none"
    if nvidia_gpu_count > 0:
        gpu_vendor = "nvidia"
        gpu_count = int(nvidia_gpu_count)
        telemetry_backend = "nvidia-smi"
    elif amd_gpu_count > 0:
        gpu_vendor = "amd"
        gpu_count = int(amd_gpu_count)
        telemetry_backend = "rocm-smi" if rocm_smi_found else "none"

    expected_runtime = {"nvidia": "cuda", "amd": "rocm"}.get(gpu_vendor)
    runtime_hint = _infer_runtime_backend_from_executable_path(executable_path)
    runtime_backend = "cpu"
    launch_error = None

    if gpu_vendor != "none" and gpu_count > 0:
        if not executable_path:
            runtime_backend = "unknown"
            launch_error = (
                f"Detected {gpu_vendor.upper()} GPUs, but no llama-server executable path is configured."
            )
        elif runtime_hint != "unknown" and expected_runtime and runtime_hint != expected_runtime:
            runtime_backend = "unknown"
            launch_error = (
                f"Detected {gpu_vendor.upper()} GPUs, but the configured llama-server path looks like a "
                f"{runtime_hint.upper()} build."
            )
        elif expected_runtime:
            runtime_backend = expected_runtime
        else:
            runtime_backend = "unknown"
            launch_error = "Could not determine a supported GPU runtime backend."

    gpu_launch_supported = runtime_backend in ("cuda", "rocm") and gpu_count > 0
    return {
        "vendor": gpu_vendor,
        "gpu_vendor": gpu_vendor,
        "telemetry_backend": telemetry_backend,
        "gpu_count": int(gpu_count),
        "runtime_backend": runtime_backend,
        "runtime_hint": runtime_hint,
        "expected_runtime_backend": expected_runtime or "cpu",
        "gpu_launch_supported": bool(gpu_launch_supported),
        "supports_tensor_split": bool(gpu_launch_supported and gpu_count >= 2),
        "supports_gpu_layers": bool(gpu_launch_supported),
        "launch_error": launch_error,
        "nvidia_smi_found": bool(nvidia_smi_found),
        "rocm_smi_found": bool(rocm_smi_found),
        "rocminfo_found": bool(rocminfo_found),
        "nvidia_gpu_count": int(nvidia_gpu_count),
        "amd_rocm_smi_gpu_count": int(amd_rocm_smi_gpu_count),
        "amd_rocminfo_gpu_count": int(amd_rocminfo_gpu_count),
        "decision_reason": "",
    }


def get_inference_runtime_capabilities(executable_path=None):
    return get_gpu_backend_state(executable_path)


def _log_gpu_launch_decision(capabilities, allowed: bool, reason: str):
    state = capabilities or {}
    print(
        "[GPU] launch decision: "
        f"vendor={state.get('gpu_vendor', 'none')} "
        f"telemetry_backend={state.get('telemetry_backend', 'none')} "
        f"runtime_backend={state.get('runtime_backend', 'unknown')} "
        f"gpu_count={int(state.get('gpu_count') or 0)} "
        f"nvidia_smi_found={int(bool(state.get('nvidia_smi_found')))} "
        f"rocm_smi_found={int(bool(state.get('rocm_smi_found')))} "
        f"rocminfo_found={int(bool(state.get('rocminfo_found')))} "
        f"launch_allowed={int(bool(allowed))} "
        f"reason={reason}"
    )


def build_llama_launch_plan(
    model_path,
    n_gpu_layers,
    *,
    executable_path=None,
    allow_tensor_split=True,
):
    capabilities = get_inference_runtime_capabilities(executable_path)
    cpu_only = bool(int(n_gpu_layers or 0) == 0)
    effective_n_gpu_layers = 0 if cpu_only else int(n_gpu_layers or 0)
    gpu_count = int(capabilities.get("gpu_count") or 0)
    extra_flags = []
    only_one_gpu = True
    model_file_size_gb = get_file_size_gb(model_path)

    if not cpu_only:
        if not capabilities.get("gpu_launch_supported"):
            error_message = None
            if capabilities.get("launch_error"):
                error_message = str(capabilities["launch_error"])
            elif capabilities.get("gpu_vendor") == "none":
                error_message = (
                    "GPU launch requested, but no supported GPU backend was detected. "
                    "Set GPU Layers to 0 for CPU-only mode."
                )
            elif gpu_count <= 0:
                error_message = (
                    f"GPU launch requested, but the detected {str(capabilities.get('gpu_vendor') or 'unknown').upper()} "
                    "backend did not report any usable GPUs."
                )
            else:
                error_message = (
                    f"GPU launch requested, but the detected {str(capabilities.get('gpu_vendor') or 'unknown').upper()} "
                    "runtime backend is unsupported or unknown."
                )

            capabilities["decision_reason"] = error_message
            _log_gpu_launch_decision(capabilities, False, error_message)
            raise RuntimeError(error_message)

        if allow_tensor_split and capabilities.get("supports_tensor_split"):
            dual_gpu_split_threshold = _get_dual_gpu_split_threshold()
            if model_file_size_gb >= dual_gpu_split_threshold:
                tensor_split = _build_tensor_split_vector(gpu_count)
                if tensor_split:
                    extra_flags += ["--tensor-split", tensor_split]
                    only_one_gpu = False

        capabilities["decision_reason"] = (
            f"Using {str(capabilities.get('runtime_backend') or 'unknown').upper()} GPU runtime for launch."
        )
        _log_gpu_launch_decision(capabilities, True, capabilities["decision_reason"])
    else:
        capabilities["decision_reason"] = "CPU-only mode requested."
        _log_gpu_launch_decision(capabilities, True, capabilities["decision_reason"])

    return {
        "capabilities": capabilities,
        "cpu_only": cpu_only,
        "n_gpu_layers": effective_n_gpu_layers,
        "gpu_count": gpu_count,
        "extra_flags": extra_flags,
        "only_one_gpu": only_one_gpu,
        "model_file_size_gb": model_file_size_gb,
    }


def _build_runtime_state(capabilities, n_gpu_layers, n_threads, cpu_only):
    runtime_backend = "cpu" if cpu_only else str((capabilities or {}).get("runtime_backend") or "unknown")
    runtime_label = "Running on CPU" if cpu_only else "Running on GPU"
    return {
        "n_gpu_layers": int(n_gpu_layers),
        "n_threads": int(n_threads),
        "runtime_mode": "cpu" if cpu_only else "gpu",
        "runtime_mode_label": runtime_label,
        "cpu_only": bool(cpu_only),
        "runtime_backend": runtime_backend,
        "gpu_vendor": str((capabilities or {}).get("gpu_vendor") or "none"),
        "telemetry_backend": str((capabilities or {}).get("telemetry_backend") or "none"),
        "gpu_count": int((capabilities or {}).get("gpu_count") or 0),
        "gpu_launch_supported": bool((capabilities or {}).get("gpu_launch_supported")),
        "supports_tensor_split": bool((capabilities or {}).get("supports_tensor_split")),
        "supports_gpu_layers": bool((capabilities or {}).get("supports_gpu_layers")),
    }


def launch_llama_instance(
    model_path, gpu_id, port, n_gpu_layers=360, n_threads=8,
    extra_flags=None, model_settings=None, only_one_gpu=False,
    ctx_size=8192, n_parallel=1, fit="on", cpu_only=False,
    executable_path=None, runtime_capabilities=None, mmproj_path=None
):
    resolved_executable = os.path.normpath(executable_path) if executable_path else _resolve_llama_server_executable()
    capabilities = runtime_capabilities or get_inference_runtime_capabilities(resolved_executable)
    command = [
        resolved_executable,
        "--model", model_path,
        "--n-gpu-layers", str(n_gpu_layers),
        "--threads", str(n_threads),
        "--host", "127.0.0.1",
        "--port", str(port),
        "--ctx-size", str(int(ctx_size)),
        "--parallel", str(int(n_parallel)),
        "--fit", str(fit),
    ]

    if not cpu_only:
        main_gpu_arg = 0 if only_one_gpu else int(gpu_id)
        command[3:3] = ["--main-gpu", str(main_gpu_arg)]

    if extra_flags:
        command += extra_flags

    if mmproj_path:
        resolved_mmproj_path = os.path.normpath(mmproj_path)
        if not os.path.isfile(resolved_mmproj_path):
            raise FileNotFoundError(f"Projector file not found: {resolved_mmproj_path}")
        command += ["--mmproj", resolved_mmproj_path]

    if model_settings:
        for flag, val in model_settings.items():
            command += [f"--{flag}", str(val)]

    env = os.environ.copy()
    for env_key in ("CUDA_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES"):
        env.pop(env_key, None)

    runtime_backend = str((capabilities or {}).get("runtime_backend") or "").lower()
    gpu_count = int((capabilities or {}).get("gpu_count") or 0)

    if runtime_backend == "cuda":
        if cpu_only:
            env["CUDA_VISIBLE_DEVICES"] = "-1"
        elif only_one_gpu:
            env["CUDA_VISIBLE_DEVICES"] = str(int(gpu_id))
        else:
            env["CUDA_VISIBLE_DEVICES"] = ",".join(str(i) for i in range(gpu_count))
    elif runtime_backend == "rocm":
        visible_value = ""
        if not cpu_only:
            if only_one_gpu:
                visible_value = str(int(gpu_id))
            else:
                visible_value = ",".join(str(i) for i in range(gpu_count))
        env["HIP_VISIBLE_DEVICES"] = visible_value
        env["ROCR_VISIBLE_DEVICES"] = visible_value

    return subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        bufsize=1,
        universal_newlines=True
    )


def wait_for_llama_server_ready(proc, port, timeout=90, log_buffer=None):
    deadline = time.time() + timeout
    urls = [
        f"http://127.0.0.1:{port}/health",
        f"http://127.0.0.1:{port}/v1/models",
    ]
    last_error = None

    while time.time() < deadline:
        failure = _detect_model_load_failure(log_buffer)
        if failure:
            return False, failure["message"], failure["error_code"], failure["retry_cpu_only"]

        if proc is None or proc.poll() is not None:
            failure = _detect_model_load_failure(log_buffer)
            if failure:
                return False, failure["message"], failure["error_code"], failure["retry_cpu_only"]

            tail = " ".join((log_buffer or [])[-4:]).strip()
            if tail:
                return False, f"llama-server exited before it became ready. Last logs: {tail[:400]}", "process_exit", False
            return False, "llama-server exited before it became ready.", "process_exit", False

        for url in urls:
            try:
                response = requests.get(url, timeout=1.5)
                if response.status_code == 200:
                    return True, None, None, False
                last_error = f"{url} returned HTTP {response.status_code}"
            except Exception as e:
                last_error = str(e)

        time.sleep(0.5)

    failure = _detect_model_load_failure(log_buffer)
    if failure:
        return False, failure["message"], failure["error_code"], failure["retry_cpu_only"]

    return False, last_error or "Timed out waiting for llama-server to become ready.", "startup_timeout", False


def stream_logs(proc, buffer_ref):
    if not proc or not proc.stdout:
        return
    try:
        for line in proc.stdout:
            if line is None:
                continue
            buffer_ref.append(line.rstrip("\r\n"))
            if len(buffer_ref) > 500:
                del buffer_ref[:-500]
    except Exception as e:
        buffer_ref.append(f"[log-reader-error] {e}")
        if len(buffer_ref) > 500:
            del buffer_ref[:-500]


def get_file_size_gb(filepath):
    try:
        p = filepath
        if p and not os.path.isabs(p):
            p = resolve_model_path(p)
        split_info = _resolve_split_gguf_info(p)
        if split_info and split_info["is_complete"]:
            size_bytes = sum(os.path.getsize(part_path) for part_path in split_info["part_paths"])
        else:
            size_bytes = os.path.getsize(p)
        return size_bytes / (1024 ** 3)
    except Exception:
        return 0


def safe_stop(proc):
    """
    Best-effort, Windows-safe shutdown.
    Returns True if we attempted to stop a running process, else False.
    Never raises.
    """
    if not proc:
        return False

    try:
        if proc.poll() is not None:
            return False

        try:
            proc.terminate()
            proc.wait(timeout=3)
            return True
        except Exception:
            pass

        try:
            proc.kill()
        except Exception:
            pass

        try:
            proc.wait(timeout=2)
        except Exception:
            pass

        return True
    except Exception:
        return False


def _reset_main_model_state():
    global current_main_model_used, current_main_model_settings, current_main_model_runtime

    current_main_model_used = ""
    current_main_model_settings = {}
    current_main_model_runtime = {}


def _reset_title_model_state():
    global current_title_model_used, current_title_model_settings, title_ready_process, title_runtime_failure_model

    current_title_model_used = ""
    current_title_model_settings = {}
    title_ready_process = None
    title_runtime_failure_model = ""


def _stop_main_model_runtime(clear_logs=True):
    global main_process

    safe_stop(main_process)
    main_process = None
    if clear_logs:
        main_log_buffer.clear()
    _reset_main_model_state()


def _stop_title_model_runtime(clear_logs=True):
    global title_process

    safe_stop(title_process)
    title_process = None
    if clear_logs:
        title_log_buffer.clear()
    _reset_title_model_state()


def _is_title_model_ready():
    return bool(
        title_process
        and title_process.poll() is None
        and title_ready_process is title_process
    )


def _monitor_title_model_readiness(proc):
    global title_ready_process, title_runtime_failure_model

    ready, ready_error, _, _ = wait_for_llama_server_ready(
        proc,
        get_llama_title_port(),
        timeout=90,
        log_buffer=title_log_buffer,
    )
    if title_process is not proc:
        return
    if ready and proc.poll() is None:
        title_ready_process = proc
        return

    print(
        "Warning: Selected title model did not become ready: "
        f"{ready_error or 'unknown startup failure'}"
    )
    failed_model_path = current_title_model_used
    _stop_title_model_runtime(clear_logs=False)
    title_runtime_failure_model = failed_model_path


def _start_selected_title_model_runtime(selected_model, executable_path, gpu_count):
    global title_process, current_title_model_used, current_title_model_settings

    if not selected_model or int(gpu_count or 0) < 2:
        _stop_title_model_runtime(clear_logs=True)
        return False

    model_path = os.path.normpath(selected_model.get("resolved_path") or resolve_model_path(selected_model.get("model_path")))
    if not os.path.isfile(model_path):
        _clear_title_model_selection()
        return False

    launch_plan = build_llama_launch_plan(
        model_path,
        360,
        executable_path=executable_path,
        allow_tensor_split=False,
    )
    capabilities = launch_plan["capabilities"]
    _stop_title_model_runtime(clear_logs=True)
    current_title_model_used = model_path
    current_title_model_settings = {"temp": 0.4}

    try:
        title_process = launch_llama_instance(
            model_path=model_path,
            gpu_id=int(gpu_count) - 1,
            port=get_llama_title_port(),
            n_gpu_layers=int(launch_plan["n_gpu_layers"]),
            n_threads=8,
            model_settings=current_title_model_settings,
            only_one_gpu=True,
            cpu_only=bool(launch_plan["cpu_only"]),
            executable_path=executable_path,
            runtime_capabilities=capabilities,
        )
        threading.Thread(
            target=stream_logs,
            args=(title_process, title_log_buffer),
            daemon=True,
        ).start()
        threading.Thread(
            target=_monitor_title_model_readiness,
            args=(title_process,),
            daemon=True,
        ).start()
        print(
            f"Started title model {_get_user_facing_model_name(model_path)} on GPU {int(gpu_count) - 1} "
            f"/ port {get_llama_title_port()} {_describe_launch_plan(launch_plan, mode='title')}"
        )
        return True
    except Exception:
        _stop_title_model_runtime(clear_logs=True)
        raise


def _is_benchmark_running():
    try:
        import benchmark_routes as benchmark_module

        bench_lock = getattr(benchmark_module, "_bench_lock", None)
        bench_state = getattr(benchmark_module, "_bench_state", None)
        if bench_lock is None or not isinstance(bench_state, dict):
            return False

        with bench_lock:
            return bool(bench_state.get("running"))
    except Exception:
        return False


def _benchmark_model_control_block():
    return jsonify({
        "status": "error",
        "message": "Benchmark is currently running. Stop benchmarks before changing model state.",
    }), 409


@model_routes.route('/start_model', methods=['POST'])
@login_required(role="admin")
def start_model():
    global main_process, main_log_buffer, current_main_model_used, current_main_model_settings
    global current_main_model_runtime
    global title_process, title_log_buffer, current_title_model_used, current_title_model_settings
    global title_runtime_failure_model

    if _is_benchmark_running():
        return _benchmark_model_control_block()

    data = request.get_json() or {}
    errors = {}
    projector_path = ""
    registry_model = None

    model_path = data.get('model_path', '').strip()
    if not model_path:
        errors['model_path'] = 'model_path is required'
    if model_path and not os.path.isabs(model_path):
        model_path = resolve_model_path(model_path)
    if model_path:
        model_path, split_info, split_error = _normalize_model_launch_path(model_path)
        if split_error:
            errors['model_path'] = split_error
        elif not os.path.isfile(model_path):
            errors['model_path'] = f"Valid model_path required (got: {model_path})"

    if model_path and not model_path.lower().endswith(".gguf"):
        errors["model_path"] = "Select a GGUF language model."
    if model_path and "model_path" not in errors:
        try:
            registry_model = _find_registry_model_by_path(model_path, enabled_present_only=False)
            if _as01((registry_model or {}).get("is_s2t")):
                errors["model_path"] = "Speech-to-Text models must be started from the Speech-to-Text tab."
            elif _as01((registry_model or {}).get("is_projector")):
                errors["model_path"] = "MMPROJ files cannot be launched as models."
            else:
                configured_mmproj_path = (registry_model or {}).get("mmproj_path")
                if configured_mmproj_path:
                    projector_path = _resolve_configured_mmproj_path(configured_mmproj_path)
        except ValueError as exc:
            errors["mmproj_path"] = str(exc)
        except Exception:
            errors["mmproj_path"] = "Could not validate the configured projector file."

    try:
        n_gpu_layers = int(data.get('n_gpu_layers', _get_db_default_int("llm.defaults.n_gpu_layers", minimum=0, maximum=1000)))
        if not (0 <= n_gpu_layers <= 1000):
            raise ValueError()
    except Exception:
        errors['n_gpu_layers'] = 'n_gpu_layers must be an integer 0–1000'

    try:
        n_threads = int(data.get('n_threads', _get_db_default_int("llm.defaults.n_cpu_threads", minimum=1, maximum=256)))
        if not (1 <= n_threads <= 256):
            raise ValueError()
    except Exception:
        errors['n_threads'] = 'n_threads must be an integer 1–256'

    try:
        temperature = float(data.get('temperature', _get_db_default_float("llm.defaults.temperature", minimum=0.0, maximum=2.0)))
        if not (0.0 <= temperature <= 2.0):
            raise ValueError()
    except Exception:
        errors['temperature'] = 'temperature must be a number 0.0–2.0'

    try:
        top_k = int(data.get('top_k', _get_db_default_int("llm.defaults.top_k", minimum=0, maximum=1000)))
        if not (0 <= top_k <= 1000):
            raise ValueError()
    except Exception:
        errors['top_k'] = 'top_k must be an integer 0–1000'

    try:
        top_p = float(data.get('top_p', _get_db_default_float("llm.defaults.top_p", minimum=0.0, maximum=1.0)))
        if not (0.0 <= top_p <= 1.0):
            raise ValueError()
    except Exception:
        errors['top_p'] = 'top_p must be a number 0.0–1.0'

    try:
        repeat_penalty = float(data.get('repeat_penalty', _get_db_default_float("llm.defaults.repeat_penalty", minimum=1.0, maximum=5.0)))
        if not (1.0 <= repeat_penalty <= 5.0):
            raise ValueError()
    except Exception:
        errors['repeat_penalty'] = 'repeat_penalty must be a number 1.0–5.0'

    try:
        seed = int(data.get('seed', _get_db_default_int("llm.defaults.seed", minimum=0, maximum=2**31 - 1)))
        if not (0 <= seed <= 2**31 - 1):
            raise ValueError()
    except Exception:
        errors['seed'] = 'seed must be an integer 0–2^31-1'

    if errors:
        return jsonify({"status": "error", "errors": errors}), 400

    try:
        exe_path = _resolve_llama_server_executable()
        launch_plan = build_llama_launch_plan(model_path, n_gpu_layers, executable_path=exe_path)
    except (FileNotFoundError, RuntimeError) as e:
        _log_gpu_launch_decision(
            get_gpu_backend_state(_get_configured_llama_server_path_or_none()),
            False,
            f"Blocked before launch: {e}",
        )
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

    capabilities = launch_plan["capabilities"]
    gpu_count = int(launch_plan["gpu_count"])
    extra_flags = list(launch_plan["extra_flags"])
    only_one_gpu = bool(launch_plan["only_one_gpu"])
    cpu_only = bool(launch_plan["cpu_only"])
    effective_n_gpu_layers = int(launch_plan["n_gpu_layers"])

    _stop_title_model_runtime(clear_logs=True)
    _stop_main_model_runtime(clear_logs=True)
    current_main_model_used = model_path
    current_main_model_settings = {
        "temp": temperature,
        "top-k": top_k,
        "top-p": top_p,
        "repeat-penalty": repeat_penalty,
        "seed": seed
    }
    current_main_model_runtime = {}

    try:
        main_process = launch_llama_instance(
            model_path=model_path,
            gpu_id=0,
            port=get_llama_main_port(),
            n_gpu_layers=effective_n_gpu_layers,
            n_threads=n_threads,
            extra_flags=extra_flags,
            model_settings=current_main_model_settings,
            only_one_gpu=only_one_gpu,
            ctx_size=49152,
            n_parallel=1,
            fit="on",
            cpu_only=cpu_only,
            executable_path=exe_path,
            runtime_capabilities=capabilities,
            mmproj_path=projector_path or None,
        )
    except Exception as e:
        _reset_main_model_state()
        return jsonify({
            "status": "error",
            "message": f"Could not start llama-server: {e}"
        }), 500

    threading.Thread(
        target=stream_logs,
        args=(main_process, main_log_buffer),
        daemon=True
    ).start()

    ready, ready_error, ready_code, retry_cpu_only = wait_for_llama_server_ready(
        main_process,
        get_llama_main_port(),
        timeout=90,
        log_buffer=main_log_buffer,
    )
    if not ready:
        _stop_main_model_runtime(clear_logs=False)
        return jsonify({
            "status": "error",
            "message": ready_error or "Main model launch failed before the server became ready.",
            "error_code": ready_code,
            "retry_cpu_only": bool(retry_cpu_only and not cpu_only),
        }), 500

    current_main_model_runtime = _build_runtime_state(
        capabilities,
        effective_n_gpu_layers,
        n_threads,
        cpu_only,
    )
    current_main_model_runtime["supports_images"] = bool(projector_path)
    current_main_model_runtime["projector_configured"] = bool(projector_path)
    current_main_model_runtime["mmproj_filename"] = os.path.basename(projector_path) if projector_path else ""
    current_main_model_runtime["display_name"] = _get_model_title(
        (registry_model or {}).get("model_name") or current_main_model_used,
        (registry_model or {}).get("friendly_name"),
    )

    selected_title_model = _get_title_model_selection(clear_invalid=True)
    if gpu_count >= 2 and only_one_gpu and selected_title_model:
        try:
            _start_selected_title_model_runtime(selected_title_model, exe_path, gpu_count)
        except Exception as exc:
            failed_title_model_path = selected_title_model.get("resolved_path") or ""
            _stop_title_model_runtime(clear_logs=True)
            title_runtime_failure_model = os.path.normpath(failed_title_model_path) if failed_title_model_path else ""
            print(f"Warning: Could not start selected title model: {exc}")
    else:
        _stop_title_model_runtime(clear_logs=True)

    print(
        f"Model loaded: {_get_user_facing_model_name(current_main_model_used)} "
        f"{_describe_launch_plan(launch_plan, mode='chat')}"
    )
    return jsonify({"status": "started"})


@model_routes.route('/stop_model', methods=['POST'])
@login_required(role="admin")
def stop_model():
    global main_process, main_log_buffer, current_main_model_used, current_main_model_settings, current_main_model_runtime
    global title_process, title_log_buffer, current_title_model_used, current_title_model_settings

    if _is_benchmark_running():
        return _benchmark_model_control_block()

    try:
        was_running = bool(main_process and main_process.poll() is None)
        unloaded_model = current_main_model_used

        print(f"Model Unloaded: {unloaded_model}")
        _stop_main_model_runtime(clear_logs=True)
        _stop_title_model_runtime(clear_logs=True)

        return jsonify({"status": "stopped", "was_running": was_running}), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Stop failed: {e.__class__.__name__}: {e}"
        }), 500


@model_routes.route('/model_status', methods=['GET'])
@login_required(roles=["admin", "user"])
def model_status():
    global main_process, current_main_model_runtime
    title_generation = _title_generation_status()
    runtime_ready = bool(
        isinstance(current_main_model_runtime, dict)
        and current_main_model_runtime.get("runtime_mode")
    )
    if main_process and main_process.poll() is None and runtime_ready:
        name = str(current_main_model_runtime.get("display_name") or "").strip()
        if not name:
            name = _get_model_title(current_main_model_used)
        return jsonify({
            "status": "running",
            "current_model": name,
            "current_model_key": _registry_path_key(current_main_model_used),
            "settings": current_main_model_settings,
            "runtime": current_main_model_runtime,
            "title_generation": title_generation,
        })
    return jsonify({
        "status": "stopped",
        "current_model": None,
        "title_generation": title_generation,
    })


@model_routes.route('/logs', methods=['GET'])
@login_required(roles=["admin", "user"])
def logs():
    return jsonify({"logs": main_log_buffer})


@model_routes.route('/clear_logs', methods=['POST'])
@login_required(role="admin")
def clear_logs():
    global main_log_buffer, title_log_buffer
    cleared_main = len(main_log_buffer)
    cleared_title = len(title_log_buffer)
    main_log_buffer.clear()
    title_log_buffer.clear()
    return jsonify({
        "status": "cleared",
        "cleared": {
            "main": cleared_main,
            "title": cleared_title
        }
    })


GPU_MONITOR_TIMEOUT_SEC = 6
ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
FLOAT_VALUE_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
ROCM_GPU_LINE_RE = re.compile(r"(?i)^\s*(?:gpu|card)\[(\d+)\][^:]*:\s*([^:]+?)\s*:\s*(.+?)\s*$")
ROCMINFO_AGENT_LINE_RE = re.compile(r"(?i)^\s*Agent\s+\d+\s*:?\s*$")
ROCMINFO_DEVICE_TYPE_GPU_RE = re.compile(r"(?i)^\s*Device\s+Type\s*:\s*GPU\b")
ROCMINFO_GFX_NAME_RE = re.compile(r"(?i)^\s*Name\s*:\s*(gfx[0-9a-f]+)\b")
ROCMINFO_MARKETING_NAME_RE = re.compile(r"(?i)^\s*Marketing\s+Name\s*:\s*AMD\b")


def _run_capture(cmd: list[str], timeout: int = GPU_MONITOR_TIMEOUT_SEC) -> dict:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except Exception as exc:
        return {
            "ok": False,
            "stdout": "",
            "stderr": str(exc),
            "returncode": None,
        }

    return {
        "ok": result.returncode == 0,
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
        "returncode": result.returncode,
    }


def _run(cmd: list[str], timeout: int = GPU_MONITOR_TIMEOUT_SEC) -> str:
    result = _run_capture(cmd, timeout=timeout)
    if not result["ok"]:
        detail = (result["stderr"] or result["stdout"] or "").strip()
        raise RuntimeError(f"cmd {cmd!r} failed: {detail}")
    return result["stdout"]


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", str(text or ""))


def _to_float_or_none(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    if not s or s.upper() == "N/A":
        return None
    match = FLOAT_VALUE_RE.search(s)
    if not match:
        return None
    try:
        return float(match.group(0))
    except Exception:
        return None


def _to_float_or_zero(value) -> float:
    parsed = _to_float_or_none(value)
    return 0.0 if parsed is None else float(parsed)


def _normalize_gpu_entry(
    index,
    name,
    vendor,
    backend,
    memory_total_mb=None,
    memory_used_mb=None,
    utilization_gpu=None,
    power_draw_w=None,
    temperature_gpu=None,
    fan_pct=None,
    performance_state=None,
):
    memory_total_mb = _to_float_or_none(memory_total_mb)
    memory_used_mb = _to_float_or_none(memory_used_mb)
    utilization_gpu = _to_float_or_none(utilization_gpu)
    power_draw_w = _to_float_or_none(power_draw_w)
    temperature_gpu = _to_float_or_none(temperature_gpu)
    fan_pct = _to_float_or_none(fan_pct)
    performance_state = str(performance_state or "").strip() or None

    return {
        "index": int(index),
        "name": str(name or f"GPU {index}").strip(),
        "vendor": str(vendor or "").strip().lower(),
        "backend": str(backend or "").strip().lower(),

        # Fields expected by current JS
        "memory_total_gb": round(memory_total_mb / 1024.0, 1) if memory_total_mb is not None else None,
        "memory_used_gb": round(memory_used_mb / 1024.0, 1) if memory_used_mb is not None else None,
        "utilization_gpu_pct": utilization_gpu,
        "power_draw_watts": power_draw_w,
        "temperature_c": temperature_gpu,

        # Legacy compatibility fields
        "memory_total_MB": memory_total_mb,
        "memory_used_MB": memory_used_mb,
        "utilization_gpu_percent": utilization_gpu,
        "utilization_gpu": utilization_gpu,
        "power_draw_W": power_draw_w,
        "temperature_gpu": temperature_gpu,
        "fan_pct": fan_pct,
        "performance_state": performance_state,
    }

def _count_nvidia_gpus() -> int:
    if shutil.which("nvidia-smi") is None:
        return 0
    result = _run_capture(["nvidia-smi", "-L"], timeout=4)
    if not result["ok"]:
        return 0
    return sum(1 for line in result["stdout"].splitlines() if line.strip().lower().startswith("gpu "))

def _split_rocminfo_agent_blocks(raw_text: str):
    text = _strip_ansi(raw_text)
    blocks = []
    current_block = []

    for line in text.splitlines():
        if ROCMINFO_AGENT_LINE_RE.match(line):
            if current_block:
                blocks.append(current_block)
            current_block = [line]
            continue

        if current_block:
            current_block.append(line)

    if current_block:
        blocks.append(current_block)
    return blocks


def _count_amd_gpus_from_rocm_smi() -> int:
    if shutil.which("rocm-smi") is None:
        return 0

    query_cmd = [
        "rocm-smi",
        "--showproductname",
        "--showtemp",
        "--showpower",
        "--showuse",
        "--showfan",
        "--showperflevel",
        "--showmeminfo",
        "vram",
        "--json",
    ]
    result = _run_capture(query_cmd, timeout=GPU_MONITOR_TIMEOUT_SEC)
    text = result["stdout"] if result["stdout"] else result["stderr"]

    gpus = _parse_amd_gpus_from_json(text)
    if gpus:
        return len(gpus)

    fallback_cmd = [
        "rocm-smi",
        "--showproductname",
        "--showtemp",
        "--showpower",
        "--showuse",
        "--showfan",
        "--showperflevel",
        "--showmeminfo",
        "vram",
    ]
    fallback = _run_capture(fallback_cmd, timeout=GPU_MONITOR_TIMEOUT_SEC)
    fallback_text = fallback["stdout"] if fallback["stdout"] else fallback["stderr"]
    return len(_parse_amd_gpus_from_text(fallback_text))


def _count_amd_gpus_from_rocminfo() -> int:
    if shutil.which("rocminfo") is None:
        return 0

    result = _run_capture(["rocminfo"], timeout=GPU_MONITOR_TIMEOUT_SEC)
    if not result["ok"]:
        return 0

    text = result["stdout"] if result["stdout"] else result["stderr"]
    agent_blocks = _split_rocminfo_agent_blocks(text)
    if agent_blocks:
        gpu_blocks = 0
        for block in agent_blocks:
            if (
                any(ROCMINFO_DEVICE_TYPE_GPU_RE.match(line) for line in block)
                or any(ROCMINFO_GFX_NAME_RE.match(line) for line in block)
                or any(ROCMINFO_MARKETING_NAME_RE.match(line) for line in block)
            ):
                gpu_blocks += 1
        if gpu_blocks > 0:
            return gpu_blocks

    direct_gpu_lines = sum(
        1
        for line in _strip_ansi(text).splitlines()
        if ROCMINFO_DEVICE_TYPE_GPU_RE.match(line)
    )
    if direct_gpu_lines > 0:
        return direct_gpu_lines

    gfx_names = set()
    for line in _strip_ansi(text).splitlines():
        match = ROCMINFO_GFX_NAME_RE.match(line)
        if match:
            gfx_names.add(match.group(1).lower())
    return len(gfx_names)

def _parse_nvidia_gpus():
    query = (
        "index,name,memory.total,"
        "memory.used,utilization.gpu,power.draw,temperature.gpu"
    )

    csv_output = _run(
        [
            "nvidia-smi",
            f"--query-gpu={query}",
            "--format=csv,noheader,nounits"
        ]
    )

    gpus = []
    for line in csv_output.strip().splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 7:
            continue

        idx, name, total_mb, used_mb, util_pct, power_w, temperature_gpu = parts[:7]
        try:
            gpus.append(
                _normalize_gpu_entry(
                    index=int(idx),
                    name=name,
                    vendor="nvidia",
                    backend="nvidia-smi",
                    memory_total_mb=_to_float_or_zero(total_mb),
                    memory_used_mb=_to_float_or_zero(used_mb),
                    utilization_gpu=_to_float_or_zero(util_pct),
                    power_draw_w=_to_float_or_zero(power_w),
                    temperature_gpu=_to_float_or_zero(temperature_gpu),
                    fan_pct=None,
                    performance_state=None,
                )
            )
        except Exception:
            continue

    return gpus


def _extract_json_payload(raw_text: str):
    text = str(raw_text or "").strip()
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None


def _flatten_metric_map(node, prefix=""):
    flattened = {}
    if isinstance(node, dict):
        for key, value in node.items():
            label = f"{prefix} {key}".strip()
            if isinstance(value, (dict, list)):
                flattened.update(_flatten_metric_map(value, label))
            else:
                flattened[label] = value
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            label = f"{prefix} {idx}".strip()
            if isinstance(value, (dict, list)):
                flattened.update(_flatten_metric_map(value, label))
            else:
                flattened[label] = value
    return flattened


def _iter_rocm_gpu_sections(node):
    sections = {}

    def _visit(value):
        if isinstance(value, dict):
            found = False
            for key, item in value.items():
                match = re.search(r"(?i)(?:card|gpu)\s*\[?(\d+)\]?", str(key))
                if not match:
                    continue
                found = True
                gpu_index = int(match.group(1))
                if isinstance(item, dict):
                    sections[gpu_index] = item
                else:
                    sections[gpu_index] = {"value": item}
            if found:
                return
            for item in value.values():
                _visit(item)
        elif isinstance(value, list):
            for item in value:
                _visit(item)

    _visit(node)
    return sections


def _find_metric_entry(metric_map, include_any, exclude_any=None):
    exclude_any = tuple(exclude_any or ())
    for label, value in metric_map.items():
        key = str(label or "").lower()
        if not any(token in key for token in include_any):
            continue
        if exclude_any and any(token in key for token in exclude_any):
            continue
        return label, value
    return None, None

def _parse_memory_mb(label, value):
    numeric = _to_float_or_none(value)
    if numeric is None:
        return None

    label_text = str(label or "").lower()
    value_text = str(value or "").lower()
    combined = f"{label_text} {value_text}"

    if "%" in combined:
        return None

    if "memory (b)" in combined or "(b)" in combined or " bytes" in combined:
        return numeric / (1024.0 * 1024.0)

    if "memory (k" in combined or " kib" in combined or " kb" in combined:
        return numeric / 1024.0

    if "memory (m" in combined or " mib" in combined or " mb" in combined:
        return numeric

    if "memory (g" in combined or " gib" in combined or " gb" in combined:
        return numeric * 1024.0

    if numeric > (1024.0 * 1024.0):
        return numeric / (1024.0 * 1024.0)

    return numeric

def _normalize_amd_name(name, index):
    value = str(name or "").strip()
    if not value or value.upper() == "N/A":
        return f"AMD GPU {index}"
    if re.fullmatch(r"0x[0-9a-f]+", value, re.IGNORECASE):
        return f"AMD GPU {index}"
    if re.fullmatch(r"gfx[0-9a-f]+", value, re.IGNORECASE):
        return f"AMD {value}"
    return value


def _build_amd_gpu(index, metric_map):
    name_label, name_value = _find_metric_entry(metric_map, ("card series",))
    if name_value is None:
        name_label, name_value = _find_metric_entry(metric_map, ("product name", "marketing name", "card model", "device name"))
    if name_value is None:
        for value in metric_map.values():
            value_text = str(value or "").strip()
            if re.fullmatch(r"gfx[0-9a-f]+", value_text, re.IGNORECASE):
                name_value = value_text
                break

    temp_label, temp_value = _find_metric_entry(metric_map, ("temperature", "temp"), exclude_any=("memory", "junction", "hotspot"))
    if temp_value is None:
        temp_label, temp_value = _find_metric_entry(metric_map, ("temperature", "temp"))

    power_label, power_value = _find_metric_entry(metric_map, ("average graphics package power",))
    if power_value is None:
        power_label, power_value = _find_metric_entry(metric_map, ("power", "avgpwr", "avg power"), exclude_any=("cap", "limit"))

    perf_label, perf_value = _find_metric_entry(metric_map, ("performance level", "perf"))
    fan_label, fan_value = _find_metric_entry(metric_map, ("fan level", "fan"))
    util_label, util_value = _find_metric_entry(metric_map, ("gpu use", "gpu%"))
    if util_value is None:
        util_label, util_value = _find_metric_entry(metric_map, ("utilization",), exclude_any=("memory",))

    mem_total_label, mem_total_value = _find_metric_entry(metric_map, ("vram total",))
    if mem_total_value is None:
        mem_total_label, mem_total_value = _find_metric_entry(metric_map, ("total memory",), exclude_any=("used", "visible"))

    mem_used_label, mem_used_value = _find_metric_entry(metric_map, ("vram used",))
    if mem_used_value is None:
        mem_used_label, mem_used_value = _find_metric_entry(metric_map, ("used memory",), exclude_any=("total",))
    if mem_total_value is None:
        for key, value in metric_map.items():
            key_l = str(key or "").lower()
            if "vram" in key_l and "total" in key_l:
                mem_total_label, mem_total_value = key, value
                break

    if mem_used_value is None:
        for key, value in metric_map.items():
            key_l = str(key or "").lower()
            if "vram" in key_l and "used" in key_l:
                mem_used_label, mem_used_value = key, value
                break

    return _normalize_gpu_entry(
        index=index,
        name=_normalize_amd_name(name_value, index),
        vendor="amd",
        backend="rocm-smi",
        memory_total_mb=_parse_memory_mb(mem_total_label, mem_total_value),
        memory_used_mb=_parse_memory_mb(mem_used_label, mem_used_value),
        utilization_gpu=_to_float_or_none(util_value),
        power_draw_w=_to_float_or_none(power_value),
        temperature_gpu=_to_float_or_none(temp_value),
        fan_pct=_to_float_or_none(fan_value),
        performance_state=perf_value,
    )


def _parse_amd_gpus_from_json(raw_text: str):
    payload = _extract_json_payload(raw_text)
    if payload is None:
        return []

    sections = _iter_rocm_gpu_sections(payload)
    gpus = []
    for gpu_index in sorted(sections.keys()):
        try:
            metric_map = _flatten_metric_map(sections[gpu_index])
            gpus.append(_build_amd_gpu(gpu_index, metric_map))
        except Exception:
            continue
    return gpus


def _parse_amd_gpus_from_text(raw_text: str):
    text = _strip_ansi(raw_text)
    metrics_by_gpu = {}

    for line in text.splitlines():
        match = ROCM_GPU_LINE_RE.match(line)
        if not match:
            continue
        gpu_index = int(match.group(1))
        label = match.group(2).strip()
        value = match.group(3).strip()
        metrics_by_gpu.setdefault(gpu_index, {})[label] = value

    header_cols = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("="):
            continue

        lower = stripped.lower()
        if lower.startswith("gpu") and ("temp" in lower or "power" in lower or "perf" in lower):
            header_cols = [part.strip() for part in re.split(r"\s{2,}|\t+", stripped) if part.strip()]
            continue

        if not header_cols or not re.match(r"^(?:\d+\b|(?:gpu|card)\[\d+\])", stripped, re.IGNORECASE):
            continue

        cols = [part.strip() for part in re.split(r"\s{2,}|\t+", stripped) if part.strip()]
        if len(cols) < 2:
            continue

        try:
            gpu_index = int(re.search(r"(\d+)", cols[0]).group(1))
        except Exception:
            continue

        row_metrics = metrics_by_gpu.setdefault(gpu_index, {})
        for idx, header in enumerate(header_cols[1:], start=1):
            if idx >= len(cols):
                break
            row_metrics[header] = cols[idx]

    gpus = []
    for gpu_index in sorted(metrics_by_gpu.keys()):
        try:
            gpus.append(_build_amd_gpu(gpu_index, metrics_by_gpu[gpu_index]))
        except Exception:
            continue
    return gpus


def _parse_amd_gpus():
    query_cmd = [
        "rocm-smi",
        "--showproductname",
        "--showtemp",
        "--showpower",
        "--showuse",
        "--showfan",
        "--showperflevel",
        "--showmeminfo",
        "vram",
        "--json",
    ]
    result = _run_capture(query_cmd, timeout=GPU_MONITOR_TIMEOUT_SEC)
    json_output = result["stdout"] if result["ok"] else ""
    gpus = _parse_amd_gpus_from_json(json_output)
    if gpus:
        return gpus

    fallback_cmd = [
        "rocm-smi",
        "--showproductname",
        "--showtemp",
        "--showpower",
        "--showuse",
        "--showfan",
        "--showperflevel",
        "--showmeminfo",
        "vram",
    ]
    fallback = _run_capture(fallback_cmd, timeout=GPU_MONITOR_TIMEOUT_SEC)
    fallback_text = fallback["stdout"] if fallback["stdout"] else fallback["stderr"]
    return _parse_amd_gpus_from_text(fallback_text)


def _normalize_telemetry_backend_selector(value: str = None) -> str:
    selected = str(value or "").strip().lower()
    if selected in ("nvidia", "nvidia-smi"):
        return "nvidia-smi"
    if selected in ("amd", "rocm", "rocm-smi"):
        return "rocm-smi"
    return "none"


def _parse_gpus(backend: str = None):
    selected_backend = _normalize_telemetry_backend_selector(backend)
    if selected_backend == "nvidia-smi":
        try:
            return _parse_nvidia_gpus()
        except Exception:
            return []
    if selected_backend == "rocm-smi":
        try:
            return _parse_amd_gpus()
        except Exception:
            return []
    return []


def _parse_nvidia_processes():
    """
    Return a list of dictionaries describing NVIDIA GPU compute processes,
    grouped by process (PID + executable name).
    """
    import csv
    import io

    def _run_csv(cmd):
        result = _run_capture(cmd, timeout=GPU_MONITOR_TIMEOUT_SEC)
        if not result["ok"]:
            return None

        stdout = (result["stdout"] or "").strip()
        if not stdout:
            return []

        reader = csv.reader(io.StringIO(stdout))
        return [row for row in reader if row]

    proc_rows = _run_csv(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,gpu_uuid,process_name,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ]
    )
    if proc_rows is None or not proc_rows:
        return []

    gpu_rows = _run_csv(
        [
            "nvidia-smi",
            "--query-gpu=index,uuid,name",
            "--format=csv,noheader,nounits",
        ]
    )

    uuid_to_gpu = {}
    if gpu_rows:
        for row in gpu_rows:
            if len(row) < 3:
                continue

            idx_raw = row[0].strip()
            uuid_raw = row[1].strip()
            name_raw = row[2].strip()

            try:
                gpu_index = int(idx_raw)
            except ValueError:
                continue

            uuid_to_gpu[uuid_raw] = {
                "gpu_index": gpu_index,
                "gpu_name": name_raw,
                "gpu_label": f"GPU {gpu_index}",
            }

    grouped = {}

    for row in proc_rows:
        if len(row) < 4:
            continue

        pid_raw = row[0].strip()
        gpu_uuid = row[1].strip()
        process_name = row[2].strip()
        mem_raw = row[3].strip()

        try:
            pid_val = int(pid_raw)
        except ValueError:
            continue

        if not process_name:
            process_name = "Unknown"

        used_mem = _to_float_or_zero(mem_raw)
        gpu_info = uuid_to_gpu.get(
            gpu_uuid,
            {
                "gpu_index": None,
                "gpu_name": gpu_uuid or "Unknown GPU",
                "gpu_label": "Unknown GPU",
            },
        )

        key = (pid_val, process_name)
        if key not in grouped:
            grouped[key] = {
                "pid": pid_val,
                "process_name": process_name,
                "gpus": [],
                "total_used_memory_MB": 0.0,
            }

        grouped[key]["gpus"].append(
            {
                "gpu_index": gpu_info["gpu_index"],
                "gpu_name": gpu_info["gpu_name"],
                "gpu_label": gpu_info["gpu_label"],
                "used_memory_MB": used_mem,
            }
        )
        grouped[key]["total_used_memory_MB"] += used_mem

    processes = list(grouped.values())

    for proc in processes:
        proc["gpus"].sort(
            key=lambda g: g["gpu_index"] if isinstance(g.get("gpu_index"), int) else 9999
        )

    processes.sort(key=lambda p: (p["pid"], p["process_name"].lower()))
    return processes


def _parse_processes(backend: str = None):
    selected_backend = _normalize_telemetry_backend_selector(backend)
    if selected_backend == "nvidia-smi":
        return _parse_nvidia_processes()
    return []


def _get_raw_gpu_output(backend: str = None):
    selected_backend = _normalize_telemetry_backend_selector(backend)
    if selected_backend == "nvidia-smi":
        result = _run_capture(["nvidia-smi"], timeout=GPU_MONITOR_TIMEOUT_SEC)
        text = result["stdout"] if result["stdout"] else result["stderr"]
        return "nvidia-smi", (text or "nvidia-smi did not return output.")
    if selected_backend == "rocm-smi":
        result = _run_capture(["rocm-smi"], timeout=GPU_MONITOR_TIMEOUT_SEC)
        text = result["stdout"] if result["stdout"] else result["stderr"]
        return "rocm-smi", (text or "rocm-smi did not return output.")
    return "GPU telemetry", "No supported GPU telemetry backend detected."


def _get_system_ram_snapshot():
    total_bytes = None
    available_bytes = None

    try:
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                total_bytes = int(stat.ullTotalPhys)
                available_bytes = int(stat.ullAvailPhys)
        else:
            page_size = os.sysconf("SC_PAGE_SIZE")
            phys_pages = os.sysconf("SC_PHYS_PAGES")
            avail_pages = os.sysconf("SC_AVPHYS_PAGES")
            total_bytes = int(page_size * phys_pages)
            available_bytes = int(page_size * avail_pages)
    except Exception:
        total_bytes = None
        available_bytes = None

    if not total_bytes or available_bytes is None:
        return {
            "available": False,
            "used_bytes": None,
            "total_bytes": None,
            "used_gb": None,
            "total_gb": None,
            "percent_used": None,
        }

    used_bytes = max(0, int(total_bytes) - int(available_bytes))
    percent_used = (used_bytes / total_bytes) * 100 if total_bytes > 0 else None
    return {
        "available": True,
        "used_bytes": used_bytes,
        "total_bytes": int(total_bytes),
        "used_gb": round(used_bytes / (1024 ** 3), 1),
        "total_gb": round(int(total_bytes) / (1024 ** 3), 1),
        "percent_used": round(percent_used, 1) if percent_used is not None else None,
    }


@model_routes.route('/nvidia_smi', methods=['GET'])
@login_required(roles=["admin", "user"])
def nvidia_smi():
    payload = {
        "timestamp_iso8601": dt.datetime.utcnow().isoformat() + "Z",
        "backend": "none",
        "vendor": "none",
        "gpu_vendor": "none",
        "telemetry_backend": "none",
        "runtime_backend": "cpu",
        "gpu_count": 0,
        "gpu_launch_supported": False,
        "gpus": [],
        "processes": [],
        "processes_supported": False,
        "processes_message": "No supported GPU telemetry backend detected.",
        "system_ram": _get_system_ram_snapshot(),
        "raw_command": "GPU telemetry",
        "raw_text": "No supported GPU telemetry backend detected.",
    }

    try:
        backend_state = get_gpu_backend_state(_get_configured_llama_server_path_or_none())
        gpu_vendor = str(backend_state.get("vendor") or "none")
        telemetry_backend = str(backend_state.get("telemetry_backend") or "none")
        raw_command, raw_text = _get_raw_gpu_output(telemetry_backend)
        gpus = _parse_gpus(telemetry_backend)
        processes = _parse_processes(telemetry_backend)

        payload.update({
            "backend": telemetry_backend,
            "vendor": gpu_vendor if gpu_vendor in ("nvidia", "amd") else "none",
            "gpu_vendor": gpu_vendor if gpu_vendor in ("nvidia", "amd") else "none",
            "telemetry_backend": telemetry_backend,
            "runtime_backend": str(backend_state.get("runtime_backend") or "cpu"),
            "gpu_count": int(backend_state.get("gpu_count") or 0),
            "gpu_launch_supported": bool(backend_state.get("gpu_launch_supported")),
            "gpus": gpus,
            "processes": processes,
            "processes_supported": telemetry_backend == "nvidia-smi",
            "processes_message": (
                "Running GPU processes are shown from nvidia-smi."
                if telemetry_backend == "nvidia-smi"
                else ("Process telemetry is currently unavailable for ROCm systems." if gpu_vendor == "amd" else payload["processes_message"])
            ),
            "raw_command": raw_command,
            "raw_text": raw_text,
        })
    except Exception as exc:
        payload["error"] = str(exc)
        payload["raw_text"] = f"GPU telemetry error: {exc}"
        print(f"[ERROR] /model/nvidia_smi: {exc}")

    return jsonify(payload)


@model_routes.route('/registry/rescan', methods=['POST'])
@login_required(role="admin")
def registry_rescan_route():
    try:
        info = registry_rescan()
        return jsonify({"status": "success", "message": "Rescan complete", "info": info})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Rescan failed: {e}"}), 500


@model_routes.route('/registry/list', methods=['GET'])
@login_required(role="admin")
def registry_list_route():
    try:
        rows = registry_list(include_disabled=True) or []
        tps_maps = _load_chat_max_tps_maps()

        for r in rows:
            try:
                r["is_enabled"] = _as01(r.get("is_enabled"))
                r["is_favorite"] = _as01(r.get("is_favorite"))
                r["allow_benchmark"] = _as01(r.get("allow_benchmark"))
                r["is_projector"] = _as01(r.get("is_projector"))
                r["is_s2t"] = _as01(r.get("is_s2t"))
                r["is_present"] = _as01(r.get("is_present"))
                for field in MODEL_PROFILE_FIELDS:
                    r[field] = _as01(r.get(field))
                r["friendly_name"] = str(r.get("friendly_name") or "")
                r["notes"] = str(r.get("notes") or "")
                r["mmproj_path"] = str(r.get("mmproj_path") or "")
                r["max_tps"] = _lookup_chat_max_tps(
                    r.get("model_name"),
                    r.get("model_path"),
                    tps_maps,
                )
            except Exception:
                pass

        return jsonify({
            "status": "success",
            "models": rows,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": f"List failed: {e}"}), 500


@model_routes.route('/registry/toggle', methods=['POST'])
@login_required(role="admin")
def registry_toggle_route():
    data = request.get_json() or {}

    model_id = data.get("id")
    fingerprint = data.get("fingerprint")
    field = (data.get("field") or "").strip()
    value = data.get("value")

    allowed = {"is_enabled", "is_favorite", "allow_benchmark", "is_projector", "is_s2t"}
    if field not in allowed:
        return jsonify({"status": "error", "message": "Invalid field"}), 400

    if not isinstance(value, int) or value not in (0, 1):
        return jsonify({
            "status": "error",
            "message": "Value must be integer 0 or 1"
        }), 400

    if model_id is None and not fingerprint:
        return jsonify({"status": "error", "message": "id or fingerprint required"}), 400

    conn = None
    cur = None
    try:
        conn = _mysql_conn()
        cur = conn.cursor()

        guarded_enable = field in {"is_enabled", "allow_benchmark"} and value == 1
        if model_id is not None:
            row_selector = "id=%s"
            row_identifier = int(model_id)
        else:
            row_selector = "fingerprint=%s"
            row_identifier = fingerprint

        if field == "is_projector" and value == 1:
            cur.execute(
                f"""
                UPDATE llm_benchmark_models
                   SET is_projector=1,
                       is_s2t=0,
                       is_enabled=0,
                       allow_benchmark=0
                 WHERE {row_selector}
                """,
                (row_identifier,),
            )
        elif field == "is_s2t" and value == 1:
            cur.execute(
                f"UPDATE llm_benchmark_models SET is_s2t=1, is_projector=0, allow_benchmark=0 WHERE {row_selector}",
                (row_identifier,),
            )
        elif field == "allow_benchmark" and value == 1:
            cur.execute(f"SELECT is_s2t, model_path FROM llm_benchmark_models WHERE {row_selector}", (row_identifier,))
            candidate = cur.fetchone()
            if candidate and (_as01(candidate[0]) or not str(candidate[1]).lower().endswith(".gguf")):
                return jsonify({"status": "error", "message": "Only language models can be benchmarked."}), 400
            cur.execute(f"UPDATE llm_benchmark_models SET allow_benchmark=1 WHERE {row_selector} AND is_projector=0", (row_identifier,))
        elif guarded_enable:
            cur.execute(
                f"UPDATE llm_benchmark_models SET {field}=1 WHERE {row_selector} AND is_projector=0",
                (row_identifier,),
            )
        else:
            cur.execute(
                f"UPDATE llm_benchmark_models SET {field}=%s WHERE {row_selector}",
                (value, row_identifier),
            )

        if cur.rowcount < 1:
            cur.execute(
                f"SELECT is_projector FROM llm_benchmark_models WHERE {row_selector} LIMIT 1",
                (row_identifier,),
            )
            existing_row = cur.fetchone()
            if existing_row is None:
                conn.rollback()
                return jsonify({"status": "error", "message": "Model registry row not found"}), 404
            if guarded_enable and _as01(existing_row[0]):
                conn.rollback()
                return jsonify({
                    "status": "error",
                    "message": "MMPROJ files cannot be enabled or benchmark-enabled.",
                }), 400

        conn.commit()

        if (field == "is_enabled" and value == 0) or (field in {"is_projector", "is_s2t"} and value == 1):
            _get_title_model_selection(clear_invalid=True)

        return jsonify({"status": "success", "field": field, "value": value})
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return jsonify({"status": "error", "message": f"Toggle failed: {e}"}), 500
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


@model_routes.route('/registry/metadata', methods=['POST'])
@login_required(role="admin")
def registry_metadata_route():
    global current_main_model_runtime

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"status": "error", "message": "A JSON object is required."}), 400

    model_id = data.get("id")
    fingerprint = str(data.get("fingerprint") or "").strip()
    if model_id is None and not fingerprint:
        return jsonify({"status": "error", "message": "id or fingerprint required"}), 400
    if model_id is not None:
        try:
            model_id = int(model_id)
        except Exception:
            return jsonify({"status": "error", "message": "Invalid model id."}), 400

    missing_fields = [field for field in MODEL_PROFILE_FIELDS if field not in data]
    if "friendly_name" not in data:
        missing_fields.append("friendly_name")
    if "notes" not in data:
        missing_fields.append("notes")
    if "mmproj_path" not in data:
        missing_fields.append("mmproj_path")
    if missing_fields:
        return jsonify({
            "status": "error",
            "message": f"Missing metadata fields: {', '.join(missing_fields)}",
        }), 400

    profile_values = {}
    for field in MODEL_PROFILE_FIELDS:
        value = data.get(field)
        if not isinstance(value, bool):
            return jsonify({
                "status": "error",
                "message": f"{field} must be true or false.",
            }), 400
        profile_values[field] = 1 if value else 0

    friendly_name_value = data.get("friendly_name")
    if not isinstance(friendly_name_value, str):
        return jsonify({"status": "error", "message": "Friendly Name must be text."}), 400
    if "\x00" in friendly_name_value or "\r" in friendly_name_value or "\n" in friendly_name_value:
        return jsonify({"status": "error", "message": "Friendly Name must be a single line."}), 400
    friendly_name_value = friendly_name_value.strip()
    if len(friendly_name_value) > 255:
        return jsonify({"status": "error", "message": "Friendly Name must be 255 characters or fewer."}), 400

    notes_value = data.get("notes")
    if not isinstance(notes_value, str):
        return jsonify({"status": "error", "message": "Notes must be text."}), 400
    if "\x00" in notes_value or "\r" in notes_value or "\n" in notes_value:
        return jsonify({"status": "error", "message": "Notes must be a single line."}), 400
    notes_value = notes_value.strip()
    if len(notes_value) > 512:
        return jsonify({"status": "error", "message": "Notes must be 512 characters or fewer."}), 400

    mmproj_value = data.get("mmproj_path")
    if not isinstance(mmproj_value, str):
        return jsonify({"status": "error", "message": "Projector path must be text."}), 400
    try:
        mmproj_value = _normalize_mmproj_path_for_storage(mmproj_value)
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    conn = None
    cur = None
    try:
        conn = _mysql_conn()
        cur = conn.cursor()
        params = (
            friendly_name_value or None,
            notes_value or None,
            profile_values["profile_general"],
            profile_values["profile_coding"],
            profile_values["profile_writing"],
            profile_values["profile_reasoning"],
            profile_values["profile_math"],
            profile_values["profile_agents"],
            profile_values["profile_images"],
            mmproj_value or None,
        )
        if model_id is not None:
            cur.execute("""
                UPDATE llm_benchmark_models
                SET friendly_name=%s,
                    notes=%s,
                    profile_general=%s,
                    profile_coding=%s,
                    profile_writing=%s,
                    profile_reasoning=%s,
                    profile_math=%s,
                    profile_agents=%s,
                    profile_images=%s,
                    mmproj_path=%s
                WHERE id=%s
            """, params + (model_id,))
        else:
            cur.execute("""
                UPDATE llm_benchmark_models
                SET friendly_name=%s,
                    notes=%s,
                    profile_general=%s,
                    profile_coding=%s,
                    profile_writing=%s,
                    profile_reasoning=%s,
                    profile_math=%s,
                    profile_agents=%s,
                    profile_images=%s,
                    mmproj_path=%s
                WHERE fingerprint=%s
            """, params + (fingerprint,))

        if model_id is not None:
            cur.execute("SELECT model_name, model_path FROM llm_benchmark_models WHERE id=%s LIMIT 1", (model_id,))
        else:
            cur.execute(
                "SELECT model_name, model_path FROM llm_benchmark_models WHERE fingerprint=%s LIMIT 1",
                (fingerprint,),
            )
        updated_row = cur.fetchone()
        if updated_row is None:
            conn.rollback()
            return jsonify({"status": "error", "message": "Model registry row not found"}), 404

        updated_model_name = updated_row[0]
        updated_model_path = updated_row[1]
        conn.commit()
        if (
            isinstance(current_main_model_runtime, dict)
            and _registry_path_key(updated_model_path) == _registry_path_key(current_main_model_used)
        ):
            current_main_model_runtime["display_name"] = _get_model_title(
                updated_model_name or updated_model_path,
                friendly_name_value,
            )
        return jsonify({
            "status": "success",
            "metadata": {
                "friendly_name": friendly_name_value,
                "notes": notes_value,
                **{field: bool(profile_values[field]) for field in MODEL_PROFILE_FIELDS},
                "mmproj_path": mmproj_value,
            },
        })
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return jsonify({"status": "error", "message": f"Metadata update failed: {exc}"}), 500
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


@model_routes.route('/registry/dropdown', methods=['GET'])
@login_required(roles=["admin", "user"])
def registry_dropdown_route():
    try:
        def load_dropdown_rows():
            conn = mysql_conn()
            cur = conn.cursor(dictionary=True)
            try:
                cur.execute("""
                    SELECT
                        id, model_name, friendly_name, model_path, file_size, is_favorite,
                        notes, profile_general, profile_coding, profile_writing,
                        profile_reasoning, profile_math, profile_agents, profile_images,
                        mmproj_path
                    FROM llm_benchmark_models
                    WHERE is_enabled=1 AND is_present=1 AND is_projector=0 AND is_s2t=0 AND LOWER(model_path) LIKE '%.gguf'
                    ORDER BY is_favorite DESC, model_name ASC
                """)
                return cur.fetchall() or []
            finally:
                try:
                    cur.close()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass

        rows = load_dropdown_rows()
        if not rows:
            conn = mysql_conn()
            cur = conn.cursor()
            try:
                cur.execute("SELECT 1 FROM llm_benchmark_models LIMIT 1")
                registry_has_rows = cur.fetchone() is not None
            finally:
                try:
                    cur.close()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass

            if not registry_has_rows:
                registry_rescan()
                rows = load_dropdown_rows()

        tps_maps = _load_chat_max_tps_maps()
        models = []
        for r in rows:
            size_gb = 0
            try:
                size_gb = float(r.get("file_size") or 0) / (1024 ** 3)
            except Exception:
                size_gb = 0

            name = r.get("model_name") or "Unknown"
            path = r.get("model_path") or ""
            is_favorite = _as01(r.get("is_favorite"))
            split_info = _resolve_split_gguf_info(resolve_model_path(path)) if path else None
            profiles = {field: _as01(r.get(field)) for field in MODEL_PROFILE_FIELDS}

            models.append({
                "name": name,
                "value": path,
                "path_key": _registry_path_key(path),
                "friendly_name": str(r.get("friendly_name") or "").strip(),
                "size_gb": round(size_gb, 2),
                "max_tps": _lookup_chat_max_tps(name, path, tps_maps),
                "is_favorite": is_favorite,
                "is_split_gguf": bool(split_info),
                "split_parts": int(split_info["shard_count"]) if split_info else 1,
                "notes": str(r.get("notes") or ""),
                "mmproj_path": str(r.get("mmproj_path") or ""),
                **profiles,
            })

        return jsonify({
            "status": "success",
            "models": models,
            "show_friendly_names": get_setting("llm.model_picker.show_friendly_names", default=False, cast=bool),
        })
    except Exception as exc:
        print(f"[ERROR] Managed model dropdown failed: {exc}")
        return jsonify({
            "status": "error",
            "message": "Could not load the managed model list.",
        }), 500
