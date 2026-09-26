import hashlib
import hmac
import json
import math
import os
import time

import requests
from flask import Blueprint, Response, jsonify, request, stream_with_context

from app_settings import get_setting
from attachment_utils import messages_have_images, normalize_openai_messages
from helpers import record_request_event
import model_routes


api_routes = Blueprint("api_routes", __name__, url_prefix="/v1")
MAX_API_REQUEST_BYTES = 16 * 1024 * 1024
MAX_API_INLINE_IMAGE_BYTES = ((MAX_API_REQUEST_BYTES - 512 * 1024) * 3) // 4


def _api_error(message, status, error_type="invalid_request_error", code=None):
    error = {"message": str(message), "type": error_type}
    if code:
        error["code"] = code
    return jsonify({"error": error}), status


def _api_is_enabled():
    try:
        return bool(get_setting("llm.api.enabled", default=False, cast=bool))
    except Exception:
        return False


def _api_authorized():
    if not _api_is_enabled():
        return False, _api_error("API access is disabled by the administrator.", 403, "access_denied", "api_disabled")
    authorization = str(request.headers.get("Authorization") or "").strip()
    if not authorization.lower().startswith("bearer "):
        return False, _api_error("A Bearer API key is required.", 401, "authentication_error", "missing_api_key")
    provided = authorization[7:].strip()
    if not provided or len(provided) > 512:
        return False, _api_error("The API key is invalid.", 401, "authentication_error", "invalid_api_key")
    try:
        expected_hash = str(get_setting("llm.api.key_hash") or "").strip().lower()
        provided_hash = hashlib.sha256(provided.encode("utf-8")).hexdigest()
    except Exception:
        expected_hash = ""
        provided_hash = ""
    if not expected_hash or not hmac.compare_digest(provided_hash, expected_hash):
        return False, _api_error("The API key is invalid.", 401, "authentication_error", "invalid_api_key")
    return True, None


def _active_model_id():
    path = str(model_routes.current_main_model_used or "").strip()
    process = model_routes.main_process
    runtime = model_routes.current_main_model_runtime or {}
    if (
        not path
        or not process
        or process.poll() is not None
        or not isinstance(runtime, dict)
        or not str(runtime.get("runtime_mode") or "").strip()
    ):
        return ""
    try:
        return model_routes._get_user_facing_model_name(path)
    except Exception:
        base = os.path.basename(path)
        return os.path.splitext(base)[0] or base


def _configured_attachment_limits():
    defaults = {
        "llm.attachments.max_files": 8,
        "llm.attachments.max_file_bytes": 1024 * 1024,
        "llm.attachments.max_total_bytes": 4 * 1024 * 1024,
    }
    values = {}
    caps = {
        "llm.attachments.max_files": 1000,
        "llm.attachments.max_file_bytes": MAX_API_INLINE_IMAGE_BYTES,
        "llm.attachments.max_total_bytes": MAX_API_INLINE_IMAGE_BYTES,
    }
    for key, default in defaults.items():
        try:
            value = int(get_setting(key, default=default, cast=int))
        except Exception:
            value = default
        values[key] = max(1, min(value, caps[key]))
    return (
        values["llm.attachments.max_files"],
        values["llm.attachments.max_file_bytes"],
        values["llm.attachments.max_total_bytes"],
    )
def _requested_stream_value(data):
    value = data.get("stream", False)
    return value if isinstance(value, bool) else False


def _read_bounded_json_object():
    if not request.is_json:
        return None, "invalid_json"
    if request.content_length is not None and request.content_length > MAX_API_REQUEST_BYTES:
        return None, "too_large"
    try:
        raw_body = request.stream.read(MAX_API_REQUEST_BYTES + 1)
    except Exception:
        return None, "invalid_json"
    if len(raw_body) > MAX_API_REQUEST_BYTES:
        return None, "too_large"
    try:
        data = json.loads(raw_body)
    except (TypeError, ValueError, UnicodeError):
        return None, "invalid_json"
    if not isinstance(data, dict):
        return None, "invalid_json"
    return data, None


def _backend_payload(data, messages):
    stream = data.get("stream", False)
    if not isinstance(stream, bool):
        raise ValueError("stream must be true or false.")
    payload = {"model": "llama", "messages": messages, "stream": stream}
    numeric_limits = {
        "max_tokens": (int, 1, 32768),
        "temperature": (float, 0.0, 2.0),
        "top_p": (float, 0.0, 1.0),
        "top_k": (int, 0, 1000),
        "repeat_penalty": (float, 1.0, 5.0),
        "seed": (int, 0, 2**31 - 1),
    }
    for key in (*numeric_limits.keys(), "stop"):
        if key not in data:
            continue
        value = data.get(key)
        if key == "stop":
            if not isinstance(value, (str, list)):
                raise ValueError("stop must be a string or array of strings.")
            stop_values = [value] if isinstance(value, str) else value
            if len(stop_values) > 16 or any(not isinstance(item, str) or len(item) > 1024 for item in stop_values):
                raise ValueError("stop may contain up to 16 strings of at most 1024 characters each.")
        else:
            caster, minimum, maximum = numeric_limits[key]
            if isinstance(value, bool):
                raise ValueError(f"{key} must be numeric.")
            if caster is int and isinstance(value, float) and not value.is_integer():
                raise ValueError(f"{key} must be an integer.")
            try:
                value = caster(value)
            except Exception as exc:
                raise ValueError(f"{key} must be numeric.") from exc
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{key} must be a finite number.")
            if value < minimum or value > maximum:
                raise ValueError(f"{key} must be between {minimum} and {maximum}.")
        payload[key] = value
    return payload


def _usage_values(payload):
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return None, None, None

    def value(*keys):
        for key in keys:
            raw = usage.get(key)
            if raw is not None:
                try:
                    return int(raw)
                except Exception:
                    return None
        return None

    return value("prompt_tokens", "input_tokens"), value("completion_tokens", "output_tokens"), value("total_tokens")


def _record_api_event(
    started_at,
    requested_model,
    status,
    http_status,
    streaming,
    *,
    active_model,
    usage=None,
):
    prompt_tokens = completion_tokens = total_tokens = None
    if isinstance(usage, dict):
        prompt_tokens, completion_tokens, total_tokens = _usage_values({"usage": usage})
    record_request_event(
        source="api",
        endpoint=request.path,
        requested_model=requested_model,
        active_model=active_model,
        status=status,
        http_status=http_status,
        duration=round(time.time() - started_at, 4),
        streaming=streaming,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def _record_api_rejection(started_at, http_status):
    record_request_event(
        source="api",
        endpoint=request.path,
        status="rejected",
        http_status=http_status,
        duration=round(time.time() - started_at, 4),
        streaming=False,
    )


@api_routes.route("/models", methods=["GET"])
def list_models():
    started_at = time.time()
    authorized, error = _api_authorized()
    if not authorized:
        _record_api_rejection(started_at, error[1] if isinstance(error, tuple) and len(error) > 1 else 401)
        return error
    model_id = _active_model_id()
    if not model_id:
        _record_api_event(
            started_at,
            None,
            "model_not_loaded",
            503,
            False,
            active_model=model_id,
        )
        return _api_error("No active model is loaded.", 503, "server_error", "model_not_loaded")
    _record_api_event(
        started_at,
        model_id,
        "completed",
        200,
        False,
        active_model=model_id,
    )
    return jsonify({
        "object": "list",
        "data": [{"id": model_id, "object": "model", "owned_by": "llm-controller-ce"}],
    })


@api_routes.route("/chat/completions", methods=["POST"])
def chat_completions():
    started_at = time.time()
    authorized, error = _api_authorized()
    if not authorized:
        _record_api_rejection(started_at, error[1] if isinstance(error, tuple) and len(error) > 1 else 401)
        return error
    data, body_error = _read_bounded_json_object()
    if body_error == "too_large":
        active_model = _active_model_id()
        _record_api_event(
            started_at,
            None,
            "invalid_request",
            413,
            False,
            active_model=active_model,
        )
        return _api_error("The request body exceeds the API size limit.", 413, code="request_too_large")
    if body_error:
        active_model = _active_model_id()
        _record_api_event(
            started_at,
            None,
            "invalid_request",
            400,
            False,
            active_model=active_model,
        )
        return _api_error("A JSON request body is required.", 400)

    active_model = _active_model_id()
    raw_requested_model = data.get("model")
    requested_model = raw_requested_model.strip() if isinstance(raw_requested_model, str) else ""
    requested_stream = _requested_stream_value(data)
    if not active_model:
        _record_api_event(
            started_at,
            requested_model,
            "model_not_loaded",
            503,
            requested_stream,
            active_model=active_model,
        )
        return _api_error("No active model is loaded.", 503, "server_error", "model_not_loaded")
    if not requested_model:
        _record_api_event(
            started_at,
            None,
            "invalid_request",
            400,
            requested_stream,
            active_model=active_model,
        )
        return _api_error("model must identify the active model.", 400, code="model_required")
    if requested_model != active_model:
        _record_api_event(
            started_at,
            requested_model,
            "invalid_model",
            400,
            requested_stream,
            active_model=active_model,
        )
        return _api_error("Only the active model is available through this API.", 400, code="model_not_active")

    try:
        max_images, max_image_bytes, max_total_image_bytes = _configured_attachment_limits()
        messages = normalize_openai_messages(
            data.get("messages"),
            max_total_chars=200000,
            max_image_bytes=max_image_bytes,
            max_images=max_images,
            max_total_image_bytes=max_total_image_bytes,
        )
        if messages_have_images(messages) and not bool((model_routes.current_main_model_runtime or {}).get("supports_images")):
            raise ValueError("The active model runtime is not configured for image understanding. Configure a compatible projector and restart the model.")
        payload = _backend_payload(data, messages)
    except ValueError as exc:
        _record_api_event(
            started_at,
            requested_model,
            "invalid_request",
            400,
            requested_stream,
            active_model=active_model,
        )
        return _api_error(str(exc), 400)

    streaming = bool(payload.get("stream"))
    try:
        backend_response = requests.post(
            model_routes.get_llama_main_completion_url(),
            json=payload,
            stream=streaming,
            timeout=600,
        )
    except requests.RequestException:
        _record_api_event(
            started_at,
            requested_model,
            "backend_unavailable",
            503,
            streaming,
            active_model=active_model,
        )
        return _api_error("The local model backend is unavailable.", 503, "server_error", "backend_unavailable")

    if backend_response.status_code >= 400:
        status = 503 if backend_response.status_code in (502, 503, 504) else 502
        backend_response.close()
        _record_api_event(
            started_at,
            requested_model,
            "backend_error",
            status,
            streaming,
            active_model=active_model,
        )
        return _api_error("The local model backend rejected the request.", status, "server_error", "backend_error")

    if not streaming:
        try:
            result = backend_response.json()
            if not isinstance(result, dict):
                raise ValueError("Invalid backend response")
        except (ValueError, requests.RequestException):
            _record_api_event(
                started_at,
                requested_model,
                "backend_invalid_response",
                502,
                False,
                active_model=active_model,
            )
            return _api_error("The local model backend returned an invalid response.", 502, "server_error", "backend_invalid_response")
        finally:
            backend_response.close()
        result["model"] = active_model
        _record_api_event(
            started_at,
            requested_model,
            "completed",
            200,
            False,
            active_model=active_model,
            usage=result.get("usage"),
        )
        return jsonify(result)

    @stream_with_context
    def relay_stream():
        status = "backend_error"
        tracked_http_status = 502
        stream_completed = False
        usage = None
        try:
            for raw_line in backend_response.iter_lines(decode_unicode=True):
                if raw_line is None:
                    continue
                line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", errors="replace")
                if line.startswith("data:"):
                    body = line[5:].strip()
                    if body == "[DONE]":
                        stream_completed = True
                        status = "completed"
                        tracked_http_status = 200
                    elif body:
                        try:
                            chunk = json.loads(body)
                            if isinstance(chunk, dict):
                                chunk["model"] = active_model
                                if isinstance(chunk.get("usage"), dict):
                                    usage = chunk.get("usage")
                                line = "data: " + json.dumps(chunk, separators=(",", ":"))
                        except ValueError:
                            pass
                yield f"{line}\n\n"
                if stream_completed:
                    break
            if not stream_completed:
                raise requests.RequestException("The model backend ended the stream before [DONE].")
        except GeneratorExit:
            if not stream_completed:
                status = "interrupted"
                tracked_http_status = 499
            raise
        except requests.RequestException:
            status = "backend_error"
            tracked_http_status = 502
            error_payload = {
                "error": {
                    "message": "The local model backend interrupted the stream.",
                    "type": "server_error",
                    "code": "backend_interrupted",
                }
            }
            yield "data: " + json.dumps(error_payload, separators=(",", ":")) + "\n\n"
            yield "data: [DONE]\n\n"
        finally:
            backend_response.close()
            _record_api_event(
                started_at,
                requested_model,
                status,
                tracked_http_status,
                True,
                active_model=active_model,
                usage=usage,
            )

    response = Response(relay_stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response
