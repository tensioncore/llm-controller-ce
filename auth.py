from flask import Blueprint, render_template, request, redirect, url_for, session, g, flash, jsonify, current_app
from flask_bcrypt import Bcrypt
from flask_wtf.csrf import generate_csrf
from werkzeug.security import check_password_hash as werkzeug_check_password_hash
from functools import wraps
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlparse, urlunparse
import hashlib
import secrets
import smtplib
import time
import threading
import config
from app_settings import get_setting
from db_mysql import mysql_conn

auth = Blueprint('auth', __name__)
bcrypt = Bcrypt()
FAILED_LOGIN_DELAY_SECONDS = 1.0
DEFAULT_SESSION_VERSION = 1
DEFAULT_RESET_TOKEN_TTL_MINUTES = 60
DEFAULT_EMAIL_CONFIRM_TOKEN_TTL_MINUTES = 1440
DEFAULT_EMAIL_TOKEN_REQUEST_COOLDOWN_SECONDS = 60
AUTH_EMAIL_THROTTLE_MAX_ENTRIES = 4096
AUTH_EMAIL_THROTTLE_PRUNE_TARGET_ENTRIES = 3072
GENERIC_RESET_REQUEST_MESSAGE = "If that email can receive a password reset, a reset link has been sent."
GENERIC_CONFIRMATION_REQUEST_MESSAGE = "If that email needs confirmation, a confirmation link has been sent."
_auth_email_request_throttle = {}
_auth_email_request_throttle_lock = threading.RLock()

MUST_CHANGE_PASSWORD_ALLOWED_ENDPOINTS = {
    'auth.force_password_change',
    'settings_routes.change_password',
    'auth.logout',
    'static',
}


def _request_wants_json():
    path = request.path or ""
    return (
        request.is_json
        or request.accept_mimetypes.best == 'application/json'
        or path.startswith(('/api/', '/chat/', '/model/', '/settings/', '/analytics/', '/benchmark/'))
    )

def get_db():
    if 'db' not in g:
        try:
            g.db = mysql_conn()
        except Exception as e:
            print("MySQL connect error:", e)
            g.db = None
    return g.db

@auth.teardown_app_request
def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def _client_ip():
    return request.remote_addr or ""

def _verify_password(stored_hash, password):
    if not stored_hash or not password:
        return False

    stored_hash = str(stored_hash).strip()

    try:
        if stored_hash.startswith(("$2a$", "$2b$", "$2y$")):
            return bcrypt.check_password_hash(stored_hash, password)

        if stored_hash.startswith(("pbkdf2:", "scrypt:")):
            return werkzeug_check_password_hash(stored_hash, password)
    except (ValueError, TypeError) as e:
        print("Password hash verification failed:", e)
        return False

    return False

def hash_auth_token(token):
    if not token:
        return None
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()

def token_is_expired(expires_at, now=None):
    if not expires_at:
        return True

    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return True

    if getattr(expires_at, "tzinfo", None) is not None:
        comparison_now = now or datetime.now(timezone.utc)
        if comparison_now.tzinfo is None:
            comparison_now = comparison_now.replace(tzinfo=timezone.utc)
    else:
        comparison_now = now or datetime.now()
        if getattr(comparison_now, "tzinfo", None) is not None:
            comparison_now = comparison_now.replace(tzinfo=None)

    return expires_at <= comparison_now

def clear_reset_token_fields(db, user_id):
    cursor = None
    try:
        cursor = db.cursor()
        cursor.execute(
            "UPDATE llm_users SET reset_token_hash=NULL, reset_token_expires_at=NULL WHERE id=%s",
            (user_id,)
        )
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass

def clear_email_confirm_token_fields(db, user_id):
    cursor = None
    try:
        cursor = db.cursor()
        cursor.execute(
            """
            UPDATE llm_users
               SET email_confirm_token_hash=NULL,
                   email_confirm_token_expires_at=NULL
             WHERE id=%s
            """,
            (user_id,)
        )
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass

def _coerce_session_version(value, default=None):
    if value is None or value == "":
        return default
    try:
        version = int(value)
    except (TypeError, ValueError):
        return default
    if version < 1:
        return default
    return version

def _coerce_positive_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(default)
    return parsed if parsed > 0 else int(default)

def _get_auth_setting(key, default=""):
    try:
        value = get_setting(key, default=default)
    except Exception as e:
        current_app.logger.warning("Unable to read auth setting %s: %s", key, e)
        return default
    return default if value is None else value

def _get_auth_setting_bool(key, default=False):
    try:
        return bool(get_setting(key, default=default, cast=bool))
    except Exception as e:
        current_app.logger.warning("Unable to read auth setting %s: %s", key, e)
        return bool(default)

def _get_auth_setting_int(key, default):
    try:
        return _coerce_positive_int(get_setting(key, default=default, cast=int), default)
    except Exception as e:
        current_app.logger.warning("Unable to read auth setting %s: %s", key, e)
        return int(default)

def _prune_auth_email_request_throttle(now, protected_keys=None):
    protected = set(protected_keys or ())

    expired_keys = [
        key for key, expires_at in _auth_email_request_throttle.items()
        if expires_at <= now and key not in protected
    ]
    for key in expired_keys:
        _auth_email_request_throttle.pop(key, None)

    if len(_auth_email_request_throttle) <= AUTH_EMAIL_THROTTLE_MAX_ENTRIES:
        return

    for key in list(_auth_email_request_throttle.keys()):
        if key in protected:
            continue
        _auth_email_request_throttle.pop(key, None)
        if len(_auth_email_request_throttle) <= AUTH_EMAIL_THROTTLE_PRUNE_TARGET_ENTRIES:
            break

def auth_email_token_request_allowed(flow, email, ip=None):
    cooldown = _get_auth_setting_int(
        "auth.email_token_request_cooldown_seconds",
        DEFAULT_EMAIL_TOKEN_REQUEST_COOLDOWN_SECONDS,
    )
    normalized_email = str(email or "").strip().lower()
    client_ip = str(ip or _client_ip() or "").strip()
    keys = []
    email_hash = hash_auth_token(normalized_email) if normalized_email else ""
    if client_ip:
        keys.append(f"{flow}:ip:{client_ip}")
    if email_hash:
        keys.append(f"{flow}:email:{email_hash}")
    if not keys:
        return True

    now = time.monotonic()
    with _auth_email_request_throttle_lock:
        _prune_auth_email_request_throttle(now, protected_keys=keys)

        retry_after = max(
            (_auth_email_request_throttle.get(key, 0) - now for key in keys),
            default=0,
        )
        if retry_after > 0:
            current_app.logger.warning(
                "Auth email request throttled: flow=%s ip=%s email_hash=%s retry_after=%ss",
                flow,
                client_ip or "<unknown>",
                email_hash[:12] if email_hash else "<none>",
                int(retry_after) + 1,
            )
            return False

        expires_at = now + cooldown
        for key in keys:
            _auth_email_request_throttle[key] = expires_at
        _prune_auth_email_request_throttle(now, protected_keys=keys)
    return True

def normalize_auth_public_base_url(value):
    raw = str(value or "").strip()
    if not raw:
        return ""

    parsed = urlparse(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    if parsed.username or parsed.password or parsed.params or parsed.query or parsed.fragment:
        return ""

    try:
        parsed.port
    except ValueError:
        return ""

    path = (parsed.path or "").rstrip("/")
    return urlunparse((parsed.scheme.lower(), parsed.netloc, path, "", "", ""))

def _configured_public_base_url(log_missing=True):
    configured = str(_get_auth_setting("auth.public_base_url", "") or "").strip()
    if not configured:
        if log_missing:
            current_app.logger.warning(
                "Auth email token not created because auth.public_base_url is missing."
            )
        return None

    normalized = normalize_auth_public_base_url(configured)
    if not normalized:
        if log_missing:
            current_app.logger.warning(
                "Auth email token not created because auth.public_base_url is invalid: %r",
                configured,
            )
        return None
    return normalized

def _public_base_url():
    base_url = _configured_public_base_url(log_missing=True)
    if not base_url:
        raise RuntimeError("auth.public_base_url is required for auth email links.")
    return base_url

def _public_auth_url(endpoint, **values):
    return f"{_public_base_url()}{url_for(endpoint, **values)}"

def _auth_email_config(log_missing=True):
    if not _get_auth_setting_bool("auth.smtp.enabled", False):
        if log_missing:
            current_app.logger.warning("Auth email token not created because auth.smtp.enabled is false.")
        return None

    host = str(_get_auth_setting("auth.smtp.host", "") or "").strip()
    from_email = str(_get_auth_setting("auth.smtp.from_email", "") or "").strip()
    if not host or not from_email:
        if log_missing:
            current_app.logger.warning("Auth email token not created because SMTP host/from_email is missing.")
        return None

    return {
        "host": host,
        "from_email": from_email,
        "port": _get_auth_setting_int("auth.smtp.port", 587),
        "username": str(_get_auth_setting("auth.smtp.username", "") or "").strip(),
        "password": str(_get_auth_setting("auth.smtp.password", "") or ""),
        "use_tls": _get_auth_setting_bool("auth.smtp.use_tls", True),
    }

def auth_email_delivery_ready():
    email_config = _auth_email_config(log_missing=True)
    base_url = _configured_public_base_url(log_missing=True)
    return email_config is not None and base_url is not None

def _send_auth_email(to_email, subject, body):
    config = _auth_email_config(log_missing=True)
    if not config:
        return False

    message = EmailMessage()
    message["From"] = config["from_email"]
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(config["host"], config["port"], timeout=15) as smtp:
            if config["use_tls"]:
                smtp.starttls()
            if config["username"]:
                smtp.login(config["username"], config["password"])
            smtp.send_message(message)
        return True
    except Exception:
        current_app.logger.exception("Auth email send failed for %s", to_email)
        return False

def _new_auth_token():
    return secrets.token_urlsafe(32)

def _token_expires_at(minutes):
    return datetime.now() + timedelta(minutes=_coerce_positive_int(minutes, 1))

def _auth_required_response():
    if _request_wants_json():
        message = "Your session has expired. Reload the page and sign in again."
        return jsonify({
            "status": "error",
            "code": "auth_required",
            "error": message,
            "message": message,
            "reload_required": True,
            "login_url": url_for('auth.login'),
        }), 401
    return redirect(url_for('auth.login'))

def _clear_session_and_require_login():
    session.clear()
    return _auth_required_response()

def _clear_session_for_invalid_auth():
    session.clear()

def _load_current_session_user():
    user_id = session.get('user_id')
    if not user_id:
        return None

    db = get_db()
    if not db:
        return None

    cursor = None
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, email, role, is_active, must_change_pw,
                   email_confirmed_at, session_version
            FROM llm_users
            WHERE id=%s
            LIMIT 1
            """,
            (user_id,)
        )
        return cursor.fetchone()
    except Exception as e:
        print("Session validation failed:", e)
        return None
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass

def validate_session_user(clear_on_failure=True):
    if 'user_id' not in session:
        return None

    current_user = _load_current_session_user()
    if not current_user or int(current_user.get('is_active') or 0) != 1:
        if clear_on_failure:
            _clear_session_for_invalid_auth()
        return None

    if current_user.get('email_confirmed_at') is None:
        if clear_on_failure:
            _clear_session_for_invalid_auth()
        return None

    db_session_version = _coerce_session_version(
        current_user.get('session_version'),
        DEFAULT_SESSION_VERSION
    )
    session_session_version = _coerce_session_version(session.get('session_version'))
    if session_session_version is None or session_session_version != db_session_version:
        if clear_on_failure:
            _clear_session_for_invalid_auth()
        return None

    session['email'] = current_user.get('email')
    session['role'] = current_user.get('role')
    session['must_change_pw'] = bool(current_user.get('must_change_pw'))
    return current_user

def login_required(roles=None, role=None):
    """
    Supports:
      @login_required()
      @login_required(role='admin')
      @login_required('admin')
      @login_required(['admin','user'])
    """
    if role is not None:
        roles = role

    if isinstance(roles, str):
        roles = [roles]

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return _auth_required_response()

            current_user = validate_session_user(clear_on_failure=True)
            if not current_user:
                return _clear_session_and_require_login()

            if session.get('must_change_pw'):
                endpoint = request.endpoint or ""
                if endpoint not in MUST_CHANGE_PASSWORD_ALLOWED_ENDPOINTS:
                    if _request_wants_json():
                        message = "Password change required before using the app."
                        return jsonify({
                            "status": "error",
                            "code": "password_change_required",
                            "error": message,
                            "message": message,
                            "redirect_url": url_for('auth.force_password_change'),
                        }), 403
                    return redirect(url_for('auth.force_password_change'))

            if roles and session.get('role') not in roles:
                if _request_wants_json():
                    return jsonify({
                        "status": "error",
                        "code": "forbidden",
                        "error": "Not authorized",
                        "message": "Not authorized",
                    }), 403
                flash("Not authorized", "error")
                return redirect(url_for('auth.login'))

            return f(*args, **kwargs)
        return decorated_function
    return decorator

def _llm_users_columns(db):
    """
    Returns a set of column names present on llm_users (lowercase).
    Kept for compatibility, even if not currently needed.
    """
    cols = set()
    cur = None
    try:
        cur = db.cursor()
        cur.execute("""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA=%s AND TABLE_NAME='llm_users'
        """, (config.DB_NAME,))
        for (name,) in cur.fetchall():
            cols.add(str(name).lower())
    except Exception as e:
        print("Could not read llm_users columns:", e)
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
    return cols

AUTH_SCHEMA_COLUMNS = {
    "email_confirmed_at": "`email_confirmed_at` datetime DEFAULT NULL",
    "pending_email": "`pending_email` varchar(255) COLLATE latin1_bin DEFAULT NULL",
    "email_confirm_token_hash": "`email_confirm_token_hash` char(64) COLLATE latin1_bin DEFAULT NULL",
    "email_confirm_token_expires_at": "`email_confirm_token_expires_at` datetime DEFAULT NULL",
    "reset_token_hash": "`reset_token_hash` char(64) COLLATE latin1_bin DEFAULT NULL",
    "reset_token_expires_at": "`reset_token_expires_at` datetime DEFAULT NULL",
    "password_changed_at": "`password_changed_at` datetime DEFAULT NULL",
    "session_version": "`session_version` int NOT NULL DEFAULT '1'",
}

AUTH_SCHEMA_INDEXES = {
    "idx_llm_users_reset_token_hash": "`reset_token_hash`",
    "idx_llm_users_email_confirm_token_hash": "`email_confirm_token_hash`",
    "idx_llm_users_pending_email": "`pending_email`",
}

AUTH_BACKFILL_MARKER = "auth.migration_backfill_v1_complete"

def _llm_users_indexes(cursor, db_name):
    cursor.execute(
        """
        SELECT INDEX_NAME, COLUMN_NAME
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME='llm_users'
        """,
        (db_name,),
    )
    index_names = set()
    indexed_columns = set()
    for index_name, column_name in cursor.fetchall() or []:
        index_names.add(index_name)
        indexed_columns.add(str(column_name).lower())
    return index_names, indexed_columns

def ensure_auth_schema_ready():
    db = None
    cursor = None
    try:
        db = mysql_conn()
        cursor = db.cursor()

        columns = _llm_users_columns(db)
        for column_name, column_definition in AUTH_SCHEMA_COLUMNS.items():
            if column_name not in columns:
                cursor.execute(f"ALTER TABLE llm_users ADD COLUMN {column_definition}")
                columns.add(column_name)

        indexes, indexed_columns = _llm_users_indexes(cursor, config.DB_NAME)
        for index_name, indexed_column in AUTH_SCHEMA_INDEXES.items():
            column_name = indexed_column.strip("`").lower()
            if index_name not in indexes and column_name not in indexed_columns:
                cursor.execute(f"ALTER TABLE llm_users ADD KEY `{index_name}` ({indexed_column})")
                indexes.add(index_name)
                indexed_columns.add(column_name)

        cursor.execute(
            "SELECT `value` FROM llm_app_settings WHERE `key`=%s LIMIT 1",
            (AUTH_BACKFILL_MARKER,),
        )
        row = cursor.fetchone()
        marker_value = str(row[0]).strip() if row else ""

        if marker_value != "1":
            cursor.execute(
                """
                UPDATE llm_users
                   SET email_confirmed_at=COALESCE(email_confirmed_at, created_at, NOW())
                 WHERE is_active=1
                   AND email_confirmed_at IS NULL
                   AND (
                        email_confirm_token_hash IS NULL
                        OR email_confirm_token_expires_at IS NULL
                        OR email_confirm_token_expires_at <= NOW()
                   )
                """
            )
            cursor.execute(
                """
                UPDATE llm_users
                   SET session_version=1
                 WHERE session_version IS NULL OR session_version < 1
                """
            )
            cursor.execute(
                """
                UPDATE llm_users
                   SET password_changed_at=COALESCE(created_at, NOW())
                 WHERE password_changed_at IS NULL
                """
            )
            cursor.execute(
                """
                INSERT INTO llm_app_settings (`key`, `value`, `value_type`, `description`, `updated_by_user_id`)
                VALUES (%s, '1', 'bool', 'Auth backfill migration completed', NULL)
                ON DUPLICATE KEY UPDATE
                    `value`='1',
                    `value_type`='bool',
                    `updated_at`=CURRENT_TIMESTAMP
                """,
                (AUTH_BACKFILL_MARKER,),
            )

        db.commit()
    except Exception as e:
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        try:
            current_app.logger.warning("Auth schema migration/backfill could not complete: %s", e)
        except Exception:
            print("Auth schema migration/backfill could not complete:", e)
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if db is not None:
            try:
                db.close()
            except Exception:
                pass

# -------------------------------------------------------------------
# NOTE:
# is_active is now STRICTLY the account-enabled / (future) email-confirmed flag:
#   1 = enabled/confirmed
#   0 = disabled/unconfirmed
# We DO NOT toggle is_active on login/logout anymore.
# -------------------------------------------------------------------

def _mark_user_online(db, user_id: int):
    """
    Compatibility stub.
    We no longer use is_active to represent "online".
    If you later add a proper field (e.g., last_seen, online, active_sessions),
    wire it here.
    """
    return

def _mark_user_offline(db, user_id: int):
    """
    Compatibility stub.
    We no longer use is_active to represent "online".
    """
    return

@auth.route('/api/me', methods=['GET'])
@login_required()
def api_me():
    return jsonify({
        "ok": True,
        "id": session.get("user_id"),
        "email": session.get("email"),
        "role": session.get("role")
    })


@auth.route('/api/csrf-token', methods=['GET'])
@login_required()
def api_csrf_token():
    return jsonify({
        "ok": True,
        "csrf_token": generate_csrf(),
    })

@auth.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    message = None

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        message = GENERIC_RESET_REQUEST_MESSAGE
        if email and not auth_email_token_request_allowed("forgot_password", email):
            return render_template('forgot_password.html', message=message)

        db = get_db()
        if db and email:
            cursor = None
            try:
                cursor = db.cursor(dictionary=True)
                cursor.execute(
                    """
                    SELECT id, email, is_active, email_confirmed_at
                    FROM llm_users
                    WHERE email=%s
                    LIMIT 1
                    """,
                    (email,)
                )
                user = cursor.fetchone()
                if (
                    user
                    and int(user.get('is_active') or 0) == 1
                    and user.get('email_confirmed_at') is not None
                    and auth_email_delivery_ready()
                ):
                    token = _new_auth_token()
                    token_hash = hash_auth_token(token)
                    expires_at = _token_expires_at(
                        _get_auth_setting_int("auth.reset_token_ttl_minutes", DEFAULT_RESET_TOKEN_TTL_MINUTES)
                    )
                    reset_url = _public_auth_url('auth.reset_password', token=token)
                    cursor.execute(
                        """
                        UPDATE llm_users
                           SET reset_token_hash=%s,
                               reset_token_expires_at=%s
                         WHERE id=%s
                        """,
                        (token_hash, expires_at, user['id'])
                    )

                    sent = _send_auth_email(
                        user['email'],
                        "Reset your LLM Controller password",
                        (
                            "A password reset was requested for your LLM Controller account.\n\n"
                            f"Reset your password here:\n{reset_url}\n\n"
                            "If you did not request this, you can ignore this email."
                        )
                    )
                    if sent:
                        db.commit()
                    else:
                        db.rollback()
                        current_app.logger.warning("Password reset token was not saved because email delivery failed.")
            except Exception:
                current_app.logger.exception("Forgot-password request failed internally.")
                try:
                    db.rollback()
                except Exception:
                    pass
            finally:
                try:
                    cursor.close()
                except Exception:
                    pass

    return render_template('forgot_password.html', message=message)

@auth.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    token_hash = hash_auth_token(token)
    user = None
    token_error = None

    db = get_db()
    if not db or not token_hash:
        token_error = "This reset link is invalid or has expired."
    else:
        cursor = None
        try:
            cursor = db.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, email, is_active, email_confirmed_at, reset_token_expires_at, session_version
                FROM llm_users
                WHERE reset_token_hash=%s
                LIMIT 1
                """,
                (token_hash,)
            )
            user = cursor.fetchone()
            if (
                not user
                or int(user.get('is_active') or 0) != 1
                or user.get('email_confirmed_at') is None
                or token_is_expired(user.get('reset_token_expires_at'))
            ):
                token_error = "This reset link is invalid or has expired."
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass

    from helpers import get_password_settings

    settings = get_password_settings()
    if token_error:
        return render_template('reset_password.html', token=token, settings=settings, token_error=token_error)

    if request.method == 'POST':
        new_pw = request.form.get('new_password', '')
        new_pw2 = request.form.get('new_password2', '')
        if new_pw != new_pw2:
            return render_template('reset_password.html', token=token, settings=settings, error="New passwords do not match.")

        from settings_routes import check_password_complexity

        errors = check_password_complexity(new_pw)
        if errors:
            return render_template('reset_password.html', token=token, settings=settings, error=" ".join(errors))

        new_hash = bcrypt.generate_password_hash(new_pw).decode('utf-8')
        cursor = None
        try:
            cursor = db.cursor()
            columns = _llm_users_columns(db)
            temp_plain_clause = "temp_password_plain=NULL," if "temp_password_plain" in columns else ""
            cursor.execute(
                f"""
                UPDATE llm_users
                   SET password_hash=%s,
                       must_change_pw=0,
                       temp_password=NULL,
                       {temp_plain_clause}
                       reset_token_hash=NULL,
                       reset_token_expires_at=NULL,
                       password_changed_at=NOW(),
                       session_version=COALESCE(session_version, 0) + 1
                 WHERE id=%s
                   AND reset_token_hash=%s
                   AND reset_token_expires_at > NOW()
                """,
                (new_hash, user['id'], token_hash)
            )
            if cursor.rowcount != 1:
                db.rollback()
                return render_template(
                    'reset_password.html',
                    token=token,
                    settings=settings,
                    token_error="This reset link is invalid, expired, or already used.",
                ), 400
            db.commit()
            return render_template(
                'auth_message.html',
                title="Password Reset Complete",
                message="Your password has been reset. You can now sign in.",
                link_url=url_for('auth.login'),
                link_text="Back to login",
            )
        except Exception:
            current_app.logger.exception("Password reset failed internally.")
            try:
                db.rollback()
            except Exception:
                pass
            return render_template('reset_password.html', token=token, settings=settings, error="Unable to reset password.")
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass

    return render_template('reset_password.html', token=token, settings=settings)

@auth.route('/confirm-email/<token>', methods=['GET'])
def confirm_email(token):
    token_hash = hash_auth_token(token)
    db = get_db()
    if not db or not token_hash:
        return render_template(
            'auth_message.html',
            title="Confirmation Failed",
            message="This confirmation link is invalid or has expired.",
            link_url=url_for('auth.login'),
            link_text="Back to login",
        ), 400

    cursor = None
    try:
        cursor = db.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, is_active, pending_email, email_confirm_token_expires_at
            FROM llm_users
            WHERE email_confirm_token_hash=%s
            LIMIT 1
            """,
            (token_hash,)
        )
        user = cursor.fetchone()
        if (
            not user
            or int(user.get('is_active') or 0) != 1
            or token_is_expired(user.get('email_confirm_token_expires_at'))
        ):
            return render_template(
                'auth_message.html',
                title="Confirmation Failed",
                message="This confirmation link is invalid or has expired.",
                link_url=url_for('auth.login'),
                link_text="Back to login",
            ), 400

        pending_email = str(user.get('pending_email') or "").strip()
        cursor.execute(
            """
            UPDATE llm_users
               SET email=CASE
                       WHEN pending_email IS NOT NULL AND TRIM(pending_email) <> ''
                       THEN pending_email
                       ELSE email
                   END,
                   pending_email=NULL,
                   email_confirmed_at=NOW(),
                   email_confirm_token_hash=NULL,
                   email_confirm_token_expires_at=NULL,
                   session_version=COALESCE(session_version, 0) + 1
              WHERE id=%s
                AND email_confirm_token_hash=%s
                AND email_confirm_token_expires_at > NOW()
            """,
            (user['id'], token_hash)
        )
        if cursor.rowcount != 1:
            db.rollback()
            return render_template(
                'auth_message.html',
                title="Confirmation Failed",
                message="This confirmation link is invalid, expired, or already used.",
                link_url=url_for('auth.login'),
                link_text="Back to login",
            ), 400
        db.commit()
        success_message = (
            "Your email address has been updated. Please sign in again."
            if pending_email
            else "Your email has been confirmed. You can now sign in."
        )
        return render_template(
            'auth_message.html',
            title="Email Confirmed",
            message=success_message,
            link_url=url_for('auth.login'),
            link_text="Back to login",
        )
    except Exception:
        current_app.logger.exception("Email confirmation failed internally.")
        try:
            db.rollback()
        except Exception:
            pass
        return render_template(
            'auth_message.html',
            title="Confirmation Failed",
            message="This confirmation link is invalid or has expired.",
            link_url=url_for('auth.login'),
            link_text="Back to login",
        ), 400
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass

@auth.route('/resend-confirmation', methods=['GET', 'POST'])
def resend_confirmation():
    message = None

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        message = GENERIC_CONFIRMATION_REQUEST_MESSAGE
        if email and not auth_email_token_request_allowed("resend_confirmation", email):
            return render_template('resend_confirmation.html', message=message)

        db = get_db()
        if db and email:
            cursor = None
            try:
                cursor = db.cursor(dictionary=True)
                cursor.execute(
                    """
                    SELECT id, email, is_active, email_confirmed_at
                    FROM llm_users
                    WHERE email=%s
                    LIMIT 1
                    """,
                    (email,)
                )
                user = cursor.fetchone()
                if (
                    user
                    and int(user.get('is_active') or 0) == 1
                    and user.get('email_confirmed_at') is None
                    and auth_email_delivery_ready()
                ):
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
                           SET email_confirm_token_hash=%s,
                               email_confirm_token_expires_at=%s
                         WHERE id=%s
                        """,
                        (token_hash, expires_at, user['id'])
                    )

                    sent = _send_auth_email(
                        user['email'],
                        "Confirm your LLM Controller email",
                        (
                            "Confirm your LLM Controller account email using this link:\n\n"
                            f"{confirm_url}\n\n"
                            "If you did not request this, you can ignore this email."
                        )
                    )
                    if sent:
                        db.commit()
                    else:
                        db.rollback()
                        current_app.logger.warning("Email confirmation token was not saved because email delivery failed.")
            except Exception:
                current_app.logger.exception("Resend-confirmation request failed internally.")
                try:
                    db.rollback()
                except Exception:
                    pass
            finally:
                try:
                    cursor.close()
                except Exception:
                    pass

    return render_template('resend_confirmation.html', message=message)

@auth.route('/login', methods=['GET', 'POST'])
def login():
    error = None

    if 'user_id' in session:
        if session.get('must_change_pw'):
            return redirect(url_for('auth.force_password_change'))
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        rememberMe = bool(request.form.get('remember'))
        ip = _client_ip()

        db = get_db()
        if not db:
            error = "Database connection error."
            return render_template('login.html', error=error)

        cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM llm_users WHERE email=%s", (email,))
        user = cursor.fetchone()

        success = 0
        password_ok = False

        if user:
            password_ok = _verify_password(user.get('password_hash'), password)

        if user and password_ok:
            if int(user.get('is_active', 0)) != 1:
                error = "This account is disabled."
            elif user.get('email_confirmed_at') is None:
                error = "Please confirm your email before signing in."
            else:
                success = 1

                session.clear()
                session['user_id'] = user['id']
                session['email'] = user['email']
                session['role'] = user['role']
                session['must_change_pw'] = bool(user.get('must_change_pw'))
                session['session_version'] = _coerce_session_version(
                    user.get('session_version'),
                    DEFAULT_SESSION_VERSION
                )

                session.permanent = rememberMe

                try:
                    cursor.execute(
                        "UPDATE llm_users SET last_login=NOW() WHERE id=%s",
                        (user['id'],)
                    )
                    db.commit()
                except Exception as e:
                    print("Failed to update last_login:", e)

                _mark_user_online(db, user['id'])

                try:
                    cursor.execute(
                        "INSERT INTO login_attempts "
                        "(username, ip_address, success) VALUES (%s,%s,%s)",
                        (email, ip, success)
                    )
                    db.commit()
                except Exception as e:
                    print("login_attempts insert failed:", e)

                if session.get('must_change_pw'):
                    return redirect(url_for('auth.force_password_change'))
                return redirect(url_for('index'))
        else:
            error = "Invalid login credentials."

        try:
            cursor.execute(
                "INSERT INTO login_attempts "
                "(username, ip_address, success) VALUES (%s,%s,%s)",
                (email, ip, success)
            )
            db.commit()
        except Exception as e:
            print("login_attempts insert failed:", e)

        # Lightweight CE anti-bruteforce delay for failed auth only.
        time.sleep(FAILED_LOGIN_DELAY_SECONDS)
        return render_template('login.html', error=error)

    return render_template('login.html')

@auth.route('/force-password-change', methods=['GET'])
@login_required()
def force_password_change():
    if not session.get('must_change_pw'):
        return redirect(url_for('index'))

    from helpers import get_password_settings

    settings = get_password_settings()
    return render_template('force_password_change.html', settings=settings)

@auth.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))
