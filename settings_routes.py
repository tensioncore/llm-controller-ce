import os
import json
import hashlib
import secrets
from datetime import datetime
from flask import Blueprint, request, jsonify, session, g, Response, current_app
from flask_wtf.csrf import validate_csrf, CSRFError
from flask_bcrypt import generate_password_hash

from auth import (
    DEFAULT_EMAIL_CONFIRM_TOKEN_TTL_MINUTES,
    _coerce_session_version,
    _get_auth_setting_int,
    _new_auth_token,
    _public_auth_url,
    _send_auth_email,
    _token_expires_at,
    _verify_password,
    auth_email_token_request_allowed,
    auth_email_delivery_ready,
    hash_auth_token,
    login_required,
    normalize_auth_public_base_url,
)
import config as app_config
from bootstrap_config import DEFAULT_BOOTSTRAP_CONFIG, get_bootstrap_config, save_bootstrap_config
from installer_service import connect_mysql_server

from db_mysql import mysql_conn
from app_settings import get_setting, set_setting, get_password_policy, get_all_settings_rows
from extensions import SOCKET_MAX_HTTP_BUFFER_BYTES
from runtime_config import get_chat_import_max_mib, validate_scan_directory_value
from smtp_credentials import (
    MAX_SMTP_PASSWORD_BYTES,
    SmtpCredentialError,
    encrypt_smtp_password,
    smtp_password_is_configured,
)
from user_preferences import (
    MAX_SYSTEM_INSTRUCTIONS_CHARS,
    get_user_system_instructions,
    set_user_system_instructions,
)

settings_routes = Blueprint('settings_routes', __name__)

PASSWORD_POLICIES = {
    "basic":    {"min_length": 6,  "require_upper": False, "require_lower": True,  "require_digit": False, "require_special": False},
    "moderate": {"min_length": 8,  "require_upper": True,  "require_lower": True,  "require_digit": True,  "require_special": False},
    "strong":   {"min_length": 12, "require_upper": True,  "require_lower": True,  "require_digit": True,  "require_special": True},
}

MAX_ATTACHMENT_TRANSPORT_BYTES = SOCKET_MAX_HTTP_BUFFER_BYTES
SMTP_PASSWORD_MASK = "********"
API_KEY_SETTING = "llm.api.key_hash"

AUTH_SETTING_DEFAULTS = [
    ("auth.email_confirm_token_ttl_minutes", "1440", "int", "Email confirmation token lifetime in minutes"),
    ("auth.email_token_request_cooldown_seconds", "60", "int", "Cooldown for auth email token requests"),
    ("auth.public_base_url", "", "string", "Public base URL for auth email links"),
    ("auth.reset_token_ttl_minutes", "60", "int", "Password reset token lifetime in minutes"),
    ("auth.smtp.enabled", "0", "bool", "Enable SMTP auth emails"),
    ("auth.smtp.from_email", "", "string", "From address for auth emails"),
    ("auth.smtp.host", "", "string", "SMTP host for auth emails"),
    ("auth.smtp.password", "", "string", "SMTP password for auth emails"),
    ("auth.smtp.port", "587", "int", "SMTP port for auth emails"),
    ("auth.smtp.use_tls", "1", "bool", "Use STARTTLS for SMTP auth emails"),
    ("auth.smtp.username", "", "string", "SMTP username for auth emails"),
]


def _csrf_failure_payload(error):
    detail = str(getattr(error, "description", "") or error or "").strip()
    lowered = detail.lower()

    if "expired" in lowered:
        code = "csrf_expired"
        message = "This page has been open too long. Reload it and try again."
    elif "session token" in lowered and "missing" in lowered:
        code = "session_expired"
        message = "Your session has expired. Reload the page and sign in again."
    else:
        code = "csrf_invalid"
        message = "This page is out of date. Reload it and try again."

    return {
        "status": "error",
        "code": code,
        "error": message,
        "message": message,
        "reload_required": True,
        "reason": "csrf",
    }


def _get_db_setting(key, cast=None):
    try:
        return get_setting(key, cast=cast)
    except Exception:
        return None


def _get_db_int_setting(key, minimum=None):
    try:
        value = int(get_setting(key, cast=int))
    except Exception:
        return None

    if minimum is not None and value < minimum:
        return None
    return value


def _clamp_attachment_transport_limit(value):
    if value is None:
        return None
    return min(int(value), MAX_ATTACHMENT_TRANSPORT_BYTES)


def _parse_bool_payload(value):
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise ValueError("Must be true or false")


def _validate_request_csrf():
    try:
        body = request.get_json(silent=True) if request.is_json else None
        csrf_token = (
            request.headers.get("X-CSRFToken")
            or (body or {}).get("csrf_token")
            or request.form.get("csrf_token")
        )
        validate_csrf(csrf_token)
    except CSRFError as error:
        return jsonify(_csrf_failure_payload(error)), 400
    return None


def _api_access_status():
    enabled = bool(_get_db_setting("llm.api.enabled", cast=bool))
    configured = bool(str(_get_db_setting(API_KEY_SETTING) or "").strip())
    return {
        "enabled": enabled,
        "key_configured": configured,
        "active": enabled and configured,
    }


def _validate_title_model_selection(model_path):
    selected = str(model_path or "").strip().replace("\\", "/")
    if not selected:
        return "", None
    if len(selected) > 4096 or "\x00" in selected or "\n" in selected or "\r" in selected:
        return "", "Select an enabled model from the managed model registry."

    db = get_db()
    if not db:
        raise RuntimeError("Database connection error")
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT model_path
              FROM llm_benchmark_models
             WHERE model_path=%s
               AND is_enabled=1
               AND is_present=1
               AND is_projector=0
             LIMIT 1
            """,
            (selected,),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
    if not row:
        return "", "Select an enabled model that is present in the managed model registry."
    return str(row.get("model_path") or "").strip().replace("\\", "/"), None


def ensure_auth_settings_seeded():
    db = get_db()
    if not db:
        return

    cursor = None
    try:
        cursor = db.cursor()
        cursor.executemany(
            """
            INSERT IGNORE INTO llm_app_settings
                (`key`, `value`, `value_type`, `description`, `updated_by_user_id`)
            VALUES (%s, %s, %s, %s, NULL)
            """,
            AUTH_SETTING_DEFAULTS,
        )
        db.commit()
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass


def _get_current_bootstrap_config():
    bootstrap_config = current_app.config.get("BOOTSTRAP_CONFIG")
    if isinstance(bootstrap_config, dict) and bootstrap_config:
        return dict(bootstrap_config)
    return dict(get_bootstrap_config())


def _sync_runtime_bootstrap_config(bootstrap_config):
    current_app.config["BOOTSTRAP_CONFIG"] = bootstrap_config
    current_app.config["SETUP_COMPLETE"] = bool(bootstrap_config.get("setup_complete"))
    app_config.DB_HOST = bootstrap_config["db_host"]
    app_config.DB_PORT = bootstrap_config["db_port"]
    app_config.DB_USER = bootstrap_config["db_user"]
    app_config.DB_PASS = bootstrap_config["db_password"]
    app_config.DB_NAME = bootstrap_config["db_name"]


def get_db():
    """
    Request-scoped MySQL connection.
    """
    if 'db' not in g:
        try:
            g.db = mysql_conn()
        except Exception as e:
            print("MySQL connect error:", e)
            g.db = None
    return g.db


def validate_password_policy(data):
    errors = {}
    level = data.get("password_policy")
    if not level or level not in ["basic", "moderate", "strong", "custom"]:
        errors["password_policy"] = "Invalid policy level."
        return errors

    if level == "custom":
        try:
            min_length = int(data.get("min_length", 0))
        except Exception:
            errors["min_length"] = "Min length must be an integer."
            min_length = 0
        if min_length < 6 or min_length > 128:
            errors["min_length"] = "Min length must be 6–128."
        for field in ["require_upper", "require_lower", "require_digit", "require_special"]:
            if field not in data:
                errors[field] = f"Missing field: {field}"
            else:
                val = data[field]
                if str(val).lower() not in ("true", "false", "1", "0"):
                    errors[field] = "Must be true or false"
    return errors


def check_password_complexity(pw: str):
    policy = get_password_policy()

    errors = []
    min_length = int(policy.get("min_length", 12))
    require_upper = bool(policy.get("require_upper", True))
    require_lower = bool(policy.get("require_lower", True))
    require_digit = bool(policy.get("require_digit", True))
    require_special = bool(policy.get("require_special", True))

    if len(pw) < min_length:
        errors.append(f"Password must be at least {min_length} characters.")
    if require_upper and not any(c.isupper() for c in pw):
        errors.append("Password must contain an uppercase letter.")
    if require_lower and not any(c.islower() for c in pw):
        errors.append("Password must contain a lowercase letter.")
    if require_digit and not any(c.isdigit() for c in pw):
        errors.append("Password must contain a digit.")
    if require_special and not any(not c.isalnum() for c in pw):
        errors.append("Password must contain a special character.")
    return errors


@settings_routes.route('/system_instructions', methods=['GET'])
@login_required()
def get_system_instructions_preference():
    user_id = session.get('user_id')
    try:
        system_instructions = get_user_system_instructions(user_id) or ""
    except Exception:
        current_app.logger.exception("Could not load the current user's System Instructions.")
        return jsonify({
            "status": "error",
            "error": "System Instructions could not be loaded."
        }), 500

    return jsonify({
        "status": "success",
        "system_instructions": system_instructions,
        "max_chars": MAX_SYSTEM_INSTRUCTIONS_CHARS,
    })


@settings_routes.route('/system_instructions', methods=['POST'])
@login_required()
def update_system_instructions_preference():
    csrf_error = _validate_request_csrf()
    if csrf_error:
        return csrf_error

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "system_instructions" not in data:
        return jsonify({
            "status": "error",
            "error": "System Instructions text is required."
        }), 400

    user_id = session.get('user_id')
    try:
        system_instructions = set_user_system_instructions(
            user_id,
            data.get("system_instructions"),
        )
    except ValueError as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400
    except Exception:
        current_app.logger.exception("Could not save the current user's System Instructions.")
        return jsonify({
            "status": "error",
            "error": "System Instructions could not be saved."
        }), 500

    return jsonify({
        "status": "success",
        "system_instructions": system_instructions or "",
        "message": (
            "System Instructions saved."
            if system_instructions
            else "System Instructions cleared."
        ),
    })


def validate_settings_payload(data):
    errors = {}

    if "scan_directory" in data:
        raw_dir = str(data.get("scan_directory", "")).strip()
        if raw_dir:
            is_valid_scan_dir, scan_dir_error = validate_scan_directory_value(raw_dir)
            if not is_valid_scan_dir:
                errors["scan_directory"] = scan_dir_error

    if "llama_server_path" in data and not str(data.get("llama_server_path", "")).strip():
        errors["llama_server_path"] = "Required"

    def check_int(field, minv, maxv):
        raw = data.get(field, "")
        if raw == "":
            errors[field] = "Required"
            return
        try:
            val = int(raw)
            if not (minv <= val <= maxv):
                raise ValueError
        except:
            errors[field] = f"Must be integer between {minv} and {maxv}"

    def check_float(field, minv, maxv):
        raw = data.get(field, "")
        try:
            val = float(raw)
            if not (minv <= val <= maxv):
                raise ValueError()
        except Exception:
            errors[field] = f"Must be number between {minv} and {maxv}"

    def check_optional_int(field, minv, maxv):
        if field not in data:
            return
        raw = data.get(field, "")
        if raw == "":
            errors[field] = "Required"
            return
        try:
            val = int(raw)
            if not (minv <= val <= maxv):
                raise ValueError
        except Exception:
            errors[field] = f"Must be integer between {minv} and {maxv}"

    check_int("n_gpu_layers", 0, 1000)
    check_int("n_cpu_threads", 1, 256)
    check_int("dual_gpu_split_threshold_gb", 0, 1024)
    check_float("temperature", 0.0, 2.0)
    check_int("top_k", 0, 1000)
    check_float("top_p", 0.0, 1.0)
    check_float("repeat_penalty", 1.0, 5.0)
    check_int("seed", 0, 2**31 - 1)
    check_optional_int("llama_main_port", 1, 65535)
    check_optional_int("llama_title_port", 1, 65535)
    if "db_port" in data and str(data.get("db_port", "")).strip():
        try:
            db_port = int(data.get("db_port"))
            if not (1 <= db_port <= 65535):
                raise ValueError
        except Exception:
            errors["db_port"] = "Must be integer between 1 and 65535"
    check_optional_int("chat_import_max_mib", 10, 1024)
    check_optional_int("attachments_max_files", 1, 1000)
    check_optional_int("attachments_max_file_bytes", 1, MAX_ATTACHMENT_TRANSPORT_BYTES)
    check_optional_int("attachments_max_total_bytes", 1, MAX_ATTACHMENT_TRANSPORT_BYTES)
    check_optional_int("attachments_max_context_chars", 1, 2**31 - 1)
    check_optional_int("attachments_chunk_max_lines", 1, 100000)
    check_optional_int("attachments_chunk_overlap_lines", 0, 100000)
    check_optional_int("auth_smtp_port", 1, 65535)
    check_optional_int("auth_reset_token_ttl_minutes", 1, 10080)
    check_optional_int("auth_email_confirm_token_ttl_minutes", 1, 43200)
    check_optional_int("auth_email_token_request_cooldown_seconds", 1, 3600)

    if "auth_public_base_url" in data:
        raw_base_url = str(data.get("auth_public_base_url", "") or "").strip()
        if raw_base_url and not normalize_auth_public_base_url(raw_base_url):
            errors["auth_public_base_url"] = "Must be a valid http(s) URL without username, password, query, or fragment"

    for field in ("auth_smtp_enabled", "auth_smtp_use_tls", "api_enabled"):
        if field in data and str(data.get(field)).lower() not in ("true", "false", "1", "0", "on", "off"):
            errors[field] = "Must be true or false"

    if "auth_smtp_password" in data:
        smtp_password = str(data.get("auth_smtp_password", "") or "")
        if smtp_password and smtp_password != SMTP_PASSWORD_MASK:
            try:
                smtp_password_bytes = smtp_password.encode("utf-8")
            except UnicodeError:
                errors["auth_smtp_password"] = "Must be valid UTF-8 text"
            else:
                if len(smtp_password_bytes) > MAX_SMTP_PASSWORD_BYTES:
                    errors["auth_smtp_password"] = (
                        f"Must be {MAX_SMTP_PASSWORD_BYTES} UTF-8 bytes or fewer"
                    )

    if "title_model_path" in data:
        title_model_path = data.get("title_model_path")
        if title_model_path is not None and not isinstance(title_model_path, str):
            errors["title_model_path"] = "Must be a model selected from the registry"

    return errors


@settings_routes.route('/change_email', methods=['POST'])
@login_required()
def change_email():
    try:
        csrf_token = request.headers.get('X-CSRFToken') or request.form.get('csrf_token')
        validate_csrf(csrf_token)
    except CSRFError as e:
        return jsonify(_csrf_failure_payload(e)), 400

    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"status": "error", "error": "Not logged in"}), 403

    data = request.get_json() or {}
    new_email = data.get("new_email", "").strip().lower()
    current_pw = data.get("current_password", "")

    if not new_email or "@" not in new_email or "." not in new_email:
        return jsonify({"status": "error", "error": "Invalid email"}), 400
    if not current_pw:
        return jsonify({"status": "error", "error": "Current password is required"}), 400

    db = get_db()
    if not db:
        return jsonify({"status": "error", "error": "Database connection error"}), 500
    cursor = db.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id, email, password_hash FROM llm_users WHERE id=%s LIMIT 1",
            (user_id,)
        )
        user = cursor.fetchone()
        if not user or not _verify_password(user.get("password_hash"), current_pw):
            return jsonify({"status": "error", "error": "Current password incorrect"}), 400

        current_email = str(user.get("email") or "").strip().lower()
        if new_email == current_email:
            return jsonify({"status": "error", "error": "New email must be different from your current email"}), 400

        cursor.execute(
            """
            SELECT id
            FROM llm_users
            WHERE id!=%s
              AND (email=%s OR pending_email=%s)
            LIMIT 1
            """,
            (user_id, new_email, new_email)
        )
        if cursor.fetchone():
            return jsonify({"status": "error", "error": "Email already in use or pending confirmation"}), 400

        if not auth_email_delivery_ready():
            return jsonify({
                "status": "error",
                "error": "Email confirmation links are not configured. Ask an admin to configure SMTP and the public base URL before changing your email."
            }), 503

        if not auth_email_token_request_allowed("change_email", new_email):
            return jsonify({
                "status": "error",
                "error": "Please wait before requesting another confirmation email."
            }), 429

        token = _new_auth_token()
        token_hash = hash_auth_token(token)
        expires_at = _token_expires_at(
            _get_auth_setting_int(
                "auth.email_confirm_token_ttl_minutes",
                DEFAULT_EMAIL_CONFIRM_TOKEN_TTL_MINUTES,
            )
        )
        confirm_url = _public_auth_url('auth.confirm_email', token=token)
        cursor.execute(
            """
            UPDATE llm_users
               SET pending_email=%s,
                   email_confirm_token_hash=%s,
                   email_confirm_token_expires_at=%s
             WHERE id=%s
            """,
            (new_email, token_hash, expires_at, user_id)
        )

        sent = _send_auth_email(
            new_email,
            "Confirm your LLM Controller email change",
            (
                "Confirm this new email address for your LLM Controller account using this link:\n\n"
                f"{confirm_url}\n\n"
                "Your existing email remains active until this link is confirmed."
            )
        )
        if not sent:
            db.rollback()
            current_app.logger.warning("Pending email change was not saved because email delivery failed.")
            return jsonify({"status": "error", "error": "Confirmation email could not be sent."}), 502

        db.commit()
        return jsonify({
            "status": "success",
            "message": "Confirmation email sent. Your current email remains active until the new address is confirmed."
        })
    except Exception:
        current_app.logger.exception("Email change request failed internally.")
        try:
            db.rollback()
        except Exception:
            pass
        return jsonify({"status": "error", "error": "Unable to start email confirmation."}), 500
    finally:
        try:
            cursor.close()
        except Exception:
            pass


@settings_routes.route('/change_password', methods=['POST'])
@login_required()
def change_password():
    try:
        csrf_token = request.headers.get('X-CSRFToken') or request.form.get('csrf_token')
        validate_csrf(csrf_token)
    except CSRFError as e:
        return jsonify(_csrf_failure_payload(e)), 400

    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"status": "error", "error": "Not logged in"}), 403

    data = request.get_json() or {}
    old_pw = data.get("old_password", "")
    new_pw = data.get("new_password", "")
    new_pw2 = data.get("new_password2", "")

    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT password_hash, session_version FROM llm_users WHERE id=%s", (user_id,))
    user = cursor.fetchone()
    if not user or not _verify_password(user['password_hash'], old_pw):
        return jsonify({"status": "error", "error": "Current password incorrect"}), 400

    if new_pw != new_pw2:
        return jsonify({"status": "error", "error": "New passwords do not match"}), 400

    errors = check_password_complexity(new_pw)
    if errors:
        return jsonify({"status": "error", "error": " ".join(errors)}), 400

    new_hash = generate_password_hash(new_pw).decode('utf-8')
    next_session_version = _coerce_session_version(user.get('session_version'), 0) + 1
    cursor.execute(
        """
        UPDATE llm_users
           SET password_hash=%s,
               must_change_pw=0,
               temp_password=NULL,
               reset_token_hash=NULL,
               reset_token_expires_at=NULL,
               password_changed_at=NOW(),
               session_version=COALESCE(session_version, 0) + 1
         WHERE id=%s
        """,
        (new_hash, user_id)
    )
    db.commit()

    session['must_change_pw'] = False
    session['session_version'] = next_session_version
    return jsonify({"status": "success", "redirect": "/"})


@settings_routes.route('/get_settings', methods=['GET'])
@login_required()
def get_settings():
    version = _get_db_setting("app.version")
    attachments_max_files = _get_db_int_setting("llm.attachments.max_files", minimum=1)
    attachments_max_file_bytes = _clamp_attachment_transport_limit(
        _get_db_int_setting("llm.attachments.max_file_bytes", minimum=1)
    )
    attachments_max_total_bytes = _clamp_attachment_transport_limit(
        _get_db_int_setting("llm.attachments.max_total_bytes", minimum=1)
    )
    policy = get_password_policy()
    payload = {
        "version": version,
        "chat_import_max_mib": get_chat_import_max_mib(),
        "attachments_max_files": attachments_max_files,
        "attachments_max_file_bytes": attachments_max_file_bytes,
        "attachments_max_total_bytes": attachments_max_total_bytes,
        "password_policy": {
            "level": policy["level"],
            "min_length": policy["min_length"],
            "require_upper": policy["require_upper"],
            "require_lower": policy["require_lower"],
            "require_digit": policy["require_digit"],
            "require_special": policy["require_special"],
        }
    }

    if session.get("role") != "admin":
        return jsonify(payload)

    ensure_auth_settings_seeded()
    bootstrap_config = _get_current_bootstrap_config()
    scan_directory = _get_db_setting("llm.scan_directory")
    llama_server_path = _get_db_setting("llm.llama_server_path")
    llama_main_port = _get_db_int_setting("llama.main.port", minimum=1)
    llama_title_port = _get_db_int_setting("llama.title.port", minimum=1)
    n_gpu_layers = _get_db_int_setting("llm.defaults.n_gpu_layers", minimum=0)
    n_cpu_threads = _get_db_int_setting("llm.defaults.n_cpu_threads", minimum=1)
    dual_gpu_split_threshold_gb = _get_db_int_setting("llama.dual_gpu_split_threshold_gb", minimum=0)
    temperature = _get_db_setting("llm.defaults.temperature", cast=float)
    top_k = _get_db_int_setting("llm.defaults.top_k", minimum=0)
    top_p = _get_db_setting("llm.defaults.top_p", cast=float)
    repeat_penalty = _get_db_setting("llm.defaults.repeat_penalty", cast=float)
    seed = _get_db_int_setting("llm.defaults.seed", minimum=0)
    attachments_max_context_chars = _get_db_int_setting("llm.attachments.max_context_chars", minimum=1)
    attachments_chunk_max_lines = _get_db_int_setting("llm.attachments.chunk_max_lines", minimum=1)
    attachments_chunk_overlap_lines = _get_db_int_setting("llm.attachments.chunk_overlap_lines", minimum=0)
    auth_smtp_enabled = bool(_get_db_setting("auth.smtp.enabled", cast=bool))
    auth_smtp_host = str(_get_db_setting("auth.smtp.host") or "")
    auth_smtp_port = _get_db_int_setting("auth.smtp.port", minimum=1) or 587
    auth_smtp_use_tls = bool(_get_db_setting("auth.smtp.use_tls", cast=bool))
    auth_smtp_username = str(_get_db_setting("auth.smtp.username") or "")
    auth_smtp_password_value = str(_get_db_setting("auth.smtp.password") or "")
    auth_smtp_password_error = ""
    try:
        auth_smtp_password_present = smtp_password_is_configured(auth_smtp_password_value)
    except SmtpCredentialError:
        auth_smtp_password_present = False
        auth_smtp_password_error = (
            "Stored SMTP password is invalid or cannot be decrypted. "
            "Enter and save a new SMTP password."
        )
    auth_smtp_from_email = str(_get_db_setting("auth.smtp.from_email") or "")
    auth_public_base_url = str(_get_db_setting("auth.public_base_url") or "")
    normalized_auth_public_base_url = normalize_auth_public_base_url(auth_public_base_url)
    auth_reset_token_ttl_minutes = _get_db_int_setting("auth.reset_token_ttl_minutes", minimum=1) or 60
    auth_email_confirm_token_ttl_minutes = _get_db_int_setting("auth.email_confirm_token_ttl_minutes", minimum=1) or 1440
    auth_email_token_request_cooldown_seconds = _get_db_int_setting("auth.email_token_request_cooldown_seconds", minimum=1) or 60
    auth_smtp_missing = not auth_smtp_enabled or not auth_smtp_host.strip() or not auth_smtp_from_email.strip()
    auth_public_base_url_missing = not normalized_auth_public_base_url
    auth_email_ready = (
        not auth_smtp_missing
        and not auth_smtp_password_error
        and not auth_public_base_url_missing
    )

    payload.update({
        "db_host": bootstrap_config.get("db_host", DEFAULT_BOOTSTRAP_CONFIG["db_host"]),
        "db_port": bootstrap_config.get("db_port", DEFAULT_BOOTSTRAP_CONFIG["db_port"]),
        "scan_directory": scan_directory,
        "llama_server_path": llama_server_path,
        "llama_main_port": llama_main_port,
        "llama_title_port": llama_title_port,
        "n_gpu_layers": n_gpu_layers,
        "n_cpu_threads": n_cpu_threads,
        "dual_gpu_split_threshold_gb": dual_gpu_split_threshold_gb,
        "temperature": temperature,
        "top_k": top_k,
        "top_p": top_p,
        "repeat_penalty": repeat_penalty,
        "seed": seed,
        "attachments_max_context_chars": attachments_max_context_chars,
        "attachments_chunk_max_lines": attachments_chunk_max_lines,
        "attachments_chunk_overlap_lines": attachments_chunk_overlap_lines,
        "title_model_path": str(_get_db_setting("llm.title_model_path") or ""),
        "api": _api_access_status(),
        "auth": {
            "email_confirmation_ready": auth_email_ready,
            "forgot_password_ready": auth_email_ready,
            "smtp_enabled": auth_smtp_enabled,
            "smtp_host": auth_smtp_host,
            "smtp_port": auth_smtp_port,
            "smtp_use_tls": auth_smtp_use_tls,
            "smtp_username": auth_smtp_username,
            "smtp_password_set": auth_smtp_password_present,
            "smtp_from_email": auth_smtp_from_email,
            "public_base_url": auth_public_base_url,
            "public_base_url_configured": bool(normalized_auth_public_base_url),
            "reset_token_ttl_minutes": auth_reset_token_ttl_minutes,
            "email_confirm_token_ttl_minutes": auth_email_confirm_token_ttl_minutes,
            "email_token_request_cooldown_seconds": auth_email_token_request_cooldown_seconds,
            "warning": (
                auth_smtp_password_error
                if auth_smtp_password_error else
                "SMTP is disabled or missing required host/from address settings."
                if auth_smtp_missing else
                "Valid public base URL is required before auth email links can be sent."
                if auth_public_base_url_missing else ""
            ),
        },
    })

    return jsonify(payload)


@settings_routes.route('/export_backup', methods=['GET'])
@login_required(role='admin')
def export_backup():
    try:
        rows = get_all_settings_rows()
        if not isinstance(rows, list):
            raise RuntimeError("Invalid settings export payload")
        rows = [
            row for row in rows
            if str(row.get("key") or "") != API_KEY_SETTING
        ]
        for row in rows:
            if str(row.get("key") or "") == "auth.smtp.password" and row.get("value"):
                row["value"] = SMTP_PASSWORD_MASK

        exported_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        filename_ts = datetime.now().strftime("%Y%m%d-%H%M%S")

        payload = {
            "backup_type": "llm-controller-settings",
            "exported_at": exported_at,
            "settings": rows,
        }

        resp = Response(
            json.dumps(payload, indent=2),
            mimetype="application/json"
        )
        resp.headers["Content-Disposition"] = (
            f"attachment; filename=llm-controller-settings-backup-{filename_ts}.json"
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except Exception as e:
        return jsonify({"status": "error", "error": f"Settings export failed: {str(e)}"}), 500


@settings_routes.route('/update_settings', methods=['POST'])
@login_required(role='admin')
def update_settings():
    csrf_error = _validate_request_csrf()
    if csrf_error:
        return csrf_error

    ensure_auth_settings_seeded()
    data = (request.get_json(silent=True) if request.is_json else request.form.to_dict()) or {}
    errors = validate_settings_payload(data)
    if errors:
        return jsonify({"status": "error", "errors": errors}), 400

    uid = session.get("user_id")

    encrypted_smtp_password = None
    if "auth_smtp_password" in data:
        submitted_smtp_password = str(data.get("auth_smtp_password", "") or "")
        if submitted_smtp_password and submitted_smtp_password != SMTP_PASSWORD_MASK:
            try:
                encrypted_smtp_password = encrypt_smtp_password(submitted_smtp_password)
            except SmtpCredentialError:
                current_app.logger.error(
                    "SMTP password was not saved because credential encryption is unavailable."
                )
                return jsonify({
                    "status": "error",
                    "error": (
                        "SMTP password could not be saved with the persistent application secret."
                    ),
                }), 500

    title_model_path_value = None
    if "title_model_path" in data:
        try:
            title_model_path_value, title_model_error = _validate_title_model_selection(
                data.get("title_model_path")
            )
        except RuntimeError as error:
            return jsonify({"status": "error", "error": str(error)}), 500
        if title_model_error:
            return jsonify({"status": "error", "errors": {"title_model_path": title_model_error}}), 400

    api_enabled_value = None
    if "api_enabled" in data:
        try:
            api_enabled_value = _parse_bool_payload(data.get("api_enabled"))
        except ValueError as error:
            return jsonify({"status": "error", "errors": {"api_enabled": str(error)}}), 400
        if api_enabled_value and not str(_get_db_setting(API_KEY_SETTING) or "").strip():
            return jsonify({
                "status": "error",
                "errors": {"api_enabled": "Generate an API key before enabling API access."},
            }), 400

    def _payload_or_current(field, key, minimum):
        raw = data.get(field)
        if raw is None or raw == "":
            current_value = _get_db_int_setting(key, minimum=minimum)
            if current_value is None:
                raise RuntimeError(f"Missing required setting: {key}")
            return current_value
        return raw

    def _payload_or_current_text(field, key):
        raw = str(data.get(field, "") or "").strip()
        if raw:
            return raw
        current_value = str(_get_db_setting(key) or "").strip()
        if not current_value:
            raise RuntimeError(f"Missing required setting: {key}")
        return current_value

    try:
        llama_server_path_value = _payload_or_current_text("llama_server_path", "llm.llama_server_path")
        llama_main_port = _payload_or_current("llama_main_port", "llama.main.port", 1)
        llama_title_port = _payload_or_current("llama_title_port", "llama.title.port", 1)
        attachments_max_files = _payload_or_current("attachments_max_files", "llm.attachments.max_files", 1)
        attachments_max_file_bytes = _clamp_attachment_transport_limit(
            _payload_or_current("attachments_max_file_bytes", "llm.attachments.max_file_bytes", 1)
        )
        attachments_max_total_bytes = _clamp_attachment_transport_limit(
            _payload_or_current("attachments_max_total_bytes", "llm.attachments.max_total_bytes", 1)
        )
        attachments_max_context_chars = _payload_or_current("attachments_max_context_chars", "llm.attachments.max_context_chars", 1)
        attachments_chunk_max_lines = _payload_or_current("attachments_chunk_max_lines", "llm.attachments.chunk_max_lines", 1)
        attachments_chunk_overlap_lines = _payload_or_current("attachments_chunk_overlap_lines", "llm.attachments.chunk_overlap_lines", 0)
        auth_smtp_port = _payload_or_current("auth_smtp_port", "auth.smtp.port", 1)
        auth_reset_token_ttl_minutes = _payload_or_current("auth_reset_token_ttl_minutes", "auth.reset_token_ttl_minutes", 1)
        auth_email_confirm_token_ttl_minutes = _payload_or_current("auth_email_confirm_token_ttl_minutes", "auth.email_confirm_token_ttl_minutes", 1)
        auth_email_token_request_cooldown_seconds = _payload_or_current("auth_email_token_request_cooldown_seconds", "auth.email_token_request_cooldown_seconds", 1)
    except RuntimeError as e:
        return jsonify({"status": "error", "error": str(e)}), 500

    bootstrap_config = _get_current_bootstrap_config()
    db_host_value = str(data.get("db_host", "") or "").strip() or str(
        bootstrap_config.get("db_host") or DEFAULT_BOOTSTRAP_CONFIG["db_host"]
    ).strip() or DEFAULT_BOOTSTRAP_CONFIG["db_host"]
    raw_db_port = str(data.get("db_port", "") or "").strip()
    if raw_db_port:
        db_port_value = int(raw_db_port)
    else:
        db_port_value = int(bootstrap_config.get("db_port") or DEFAULT_BOOTSTRAP_CONFIG["db_port"])
    current_db_host_value = str(
        bootstrap_config.get("db_host") or DEFAULT_BOOTSTRAP_CONFIG["db_host"]
    ).strip() or DEFAULT_BOOTSTRAP_CONFIG["db_host"]
    current_db_port_value = int(bootstrap_config.get("db_port") or DEFAULT_BOOTSTRAP_CONFIG["db_port"])

    if db_host_value != current_db_host_value or int(db_port_value) != current_db_port_value:
        test_conn = None
        try:
            test_conn = connect_mysql_server(
                db_host_value,
                str(bootstrap_config.get("db_user") or DEFAULT_BOOTSTRAP_CONFIG["db_user"]),
                str(bootstrap_config.get("db_password") or DEFAULT_BOOTSTRAP_CONFIG["db_password"]),
                database=str(bootstrap_config.get("db_name") or DEFAULT_BOOTSTRAP_CONFIG["db_name"]),
                db_port=int(db_port_value),
            )
        except Exception:
            error_field = "db_host" if db_host_value != current_db_host_value else "db_port"
            return jsonify({
                "status": "error",
                "errors": {
                    error_field: f"Unable to connect to MySQL at {db_host_value}:{db_port_value} with the current database settings."
                }
            }), 400
        finally:
            if test_conn is not None:
                try:
                    test_conn.close()
                except Exception:
                    pass

    scan_directory_value = str(data.get("scan_directory", "") or "").strip()
    if not scan_directory_value:
        scan_directory_value = str(_get_db_setting("llm.scan_directory") or "").strip()
    if not scan_directory_value:
        return jsonify({"status": "error", "error": "Missing required setting: llm.scan_directory"}), 500

    set_setting("llm.scan_directory", scan_directory_value, "string", updated_by_user_id=uid)
    set_setting("llm.llama_server_path", llama_server_path_value, "string", updated_by_user_id=uid)
    set_setting("llama.main.port", int(llama_main_port), "int", updated_by_user_id=uid)
    set_setting("llama.title.port", int(llama_title_port), "int", updated_by_user_id=uid)
    set_setting("llm.defaults.n_gpu_layers", int(data["n_gpu_layers"]), "int", updated_by_user_id=uid)
    set_setting("llm.defaults.n_cpu_threads", int(data["n_cpu_threads"]), "int", updated_by_user_id=uid)
    set_setting("llama.dual_gpu_split_threshold_gb", int(data["dual_gpu_split_threshold_gb"]), "int", updated_by_user_id=uid)
    set_setting("llm.defaults.temperature", float(data["temperature"]), "float", updated_by_user_id=uid)
    set_setting("llm.defaults.top_k", int(data["top_k"]), "int", updated_by_user_id=uid)
    set_setting("llm.defaults.top_p", float(data["top_p"]), "float", updated_by_user_id=uid)
    set_setting("llm.defaults.repeat_penalty", float(data["repeat_penalty"]), "float", updated_by_user_id=uid)
    set_setting("llm.defaults.seed", int(data["seed"]), "int", updated_by_user_id=uid)
    if "chat_import_max_mib" in data:
        set_setting("llm.chat_import.max_mib", int(data["chat_import_max_mib"]), "int", updated_by_user_id=uid)
    set_setting("llm.attachments.max_files", int(attachments_max_files), "int", updated_by_user_id=uid)
    set_setting("llm.attachments.max_file_bytes", int(attachments_max_file_bytes), "int", updated_by_user_id=uid)
    set_setting("llm.attachments.max_total_bytes", int(attachments_max_total_bytes), "int", updated_by_user_id=uid)
    set_setting("llm.attachments.max_context_chars", int(attachments_max_context_chars), "int", updated_by_user_id=uid)
    set_setting("llm.attachments.chunk_max_lines", int(attachments_chunk_max_lines), "int", updated_by_user_id=uid)
    set_setting("llm.attachments.chunk_overlap_lines", int(attachments_chunk_overlap_lines), "int", updated_by_user_id=uid)
    if title_model_path_value is not None:
        set_setting("llm.title_model_path", title_model_path_value, "string", updated_by_user_id=uid)
    if api_enabled_value is not None:
        set_setting("llm.api.enabled", api_enabled_value, "bool", updated_by_user_id=uid)
    if "auth_smtp_enabled" in data:
        set_setting("auth.smtp.enabled", data.get("auth_smtp_enabled", "false"), "bool", updated_by_user_id=uid)
    if "auth_smtp_host" in data:
        set_setting("auth.smtp.host", str(data.get("auth_smtp_host", "") or "").strip(), "string", updated_by_user_id=uid)
    if "auth_smtp_port" in data:
        set_setting("auth.smtp.port", int(auth_smtp_port), "int", updated_by_user_id=uid)
    if "auth_smtp_use_tls" in data:
        set_setting("auth.smtp.use_tls", data.get("auth_smtp_use_tls", "true"), "bool", updated_by_user_id=uid)
    if "auth_smtp_username" in data:
        set_setting("auth.smtp.username", str(data.get("auth_smtp_username", "") or "").strip(), "string", updated_by_user_id=uid)
    if encrypted_smtp_password is not None:
        set_setting(
            "auth.smtp.password",
            encrypted_smtp_password,
            "string",
            updated_by_user_id=uid,
        )
    if "auth_smtp_from_email" in data:
        set_setting("auth.smtp.from_email", str(data.get("auth_smtp_from_email", "") or "").strip(), "string", updated_by_user_id=uid)
    if "auth_public_base_url" in data:
        set_setting("auth.public_base_url", normalize_auth_public_base_url(data.get("auth_public_base_url")), "string", updated_by_user_id=uid)
    if "auth_reset_token_ttl_minutes" in data:
        set_setting("auth.reset_token_ttl_minutes", int(auth_reset_token_ttl_minutes), "int", updated_by_user_id=uid)
    if "auth_email_confirm_token_ttl_minutes" in data:
        set_setting("auth.email_confirm_token_ttl_minutes", int(auth_email_confirm_token_ttl_minutes), "int", updated_by_user_id=uid)
    if "auth_email_token_request_cooldown_seconds" in data:
        set_setting("auth.email_token_request_cooldown_seconds", int(auth_email_token_request_cooldown_seconds), "int", updated_by_user_id=uid)

    bootstrap_config["db_host"] = db_host_value
    bootstrap_config["db_port"] = int(db_port_value)
    saved_bootstrap_config = save_bootstrap_config(bootstrap_config)
    _sync_runtime_bootstrap_config(saved_bootstrap_config)

    return jsonify({"status": "success"})


@settings_routes.route('/api_key/regenerate', methods=['POST'])
@login_required(role='admin')
def regenerate_api_key():
    csrf_error = _validate_request_csrf()
    if csrf_error:
        return csrf_error

    api_key = "llmc_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    try:
        set_setting(
            API_KEY_SETTING,
            key_hash,
            "string",
            updated_by_user_id=session.get("user_id"),
        )
        status = _api_access_status()
    except Exception:
        current_app.logger.exception("API key regeneration failed internally.")
        return jsonify({"status": "error", "error": "Unable to generate the API key."}), 500

    response = jsonify({"status": "success", "api_key": api_key, "api": status})
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@settings_routes.route('/api_key/revoke', methods=['POST'])
@login_required(role='admin')
def revoke_api_key():
    csrf_error = _validate_request_csrf()
    if csrf_error:
        return csrf_error

    try:
        uid = session.get("user_id")
        set_setting("llm.api.enabled", False, "bool", updated_by_user_id=uid)
        set_setting(API_KEY_SETTING, "", "string", updated_by_user_id=uid)
    except Exception:
        current_app.logger.exception("API key revocation failed internally.")
        return jsonify({"status": "error", "error": "Unable to revoke the API key."}), 500

    response = jsonify({"status": "success", "api": _api_access_status()})
    response.headers["Cache-Control"] = "no-store"
    return response


@settings_routes.route('/update_password_policy', methods=['POST'])
@login_required(role='admin')
def update_password_policy():
    try:
        csrf_token = request.headers.get('X-CSRFToken') or request.form.get('csrf_token')
        validate_csrf(csrf_token)
    except CSRFError as e:
        return jsonify(_csrf_failure_payload(e)), 400

    data = request.get_json() if request.is_json else request.form.to_dict()

    # Normalize checkbox/bool-ish inputs into true/false strings
    for field in ["require_upper", "require_lower", "require_digit", "require_special"]:
        if field in data:
            val = data[field]
            if isinstance(val, bool):
                data[field] = str(val).lower()
            elif isinstance(val, str):
                data[field] = "true" if val.lower() in ("true", "on", "1") else "false"
            else:
                data[field] = "false"
        else:
            data[field] = "false"

    errors = validate_password_policy(data)
    if errors:
        return jsonify({"status": "error", "errors": errors}), 400

    uid = session.get("user_id")
    level = data["password_policy"]

    set_setting("security.password_policy.level", level, "string", updated_by_user_id=uid)

    if level == "custom":
        set_setting("security.password_policy.min_length", int(data.get("min_length", 12)), "int", updated_by_user_id=uid)
        set_setting("security.password_policy.require_upper", data.get("require_upper", "false"), "bool", updated_by_user_id=uid)
        set_setting("security.password_policy.require_lower", data.get("require_lower", "false"), "bool", updated_by_user_id=uid)
        set_setting("security.password_policy.require_digit", data.get("require_digit", "false"), "bool", updated_by_user_id=uid)
        set_setting("security.password_policy.require_special", data.get("require_special", "false"), "bool", updated_by_user_id=uid)
    else:
        policy = PASSWORD_POLICIES[level]
        set_setting("security.password_policy.min_length", int(policy["min_length"]), "int", updated_by_user_id=uid)
        set_setting("security.password_policy.require_upper", policy["require_upper"], "bool", updated_by_user_id=uid)
        set_setting("security.password_policy.require_lower", policy["require_lower"], "bool", updated_by_user_id=uid)
        set_setting("security.password_policy.require_digit", policy["require_digit"], "bool", updated_by_user_id=uid)
        set_setting("security.password_policy.require_special", policy["require_special"], "bool", updated_by_user_id=uid)

    return jsonify({"status": "success"})
