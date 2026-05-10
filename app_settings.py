import json
import threading
import time

from db_mysql import mysql_conn

# -------------------------------------------------------------------
# Small in-process TTL cache for settings reads
# -------------------------------------------------------------------
# Cache stores DB row data (value, value_type) or a negative-cache (found=False).
# This avoids hitting MySQL on every get_setting() call while keeping updates fast.
_SETTINGS_CACHE_TTL_SEC = 2.0  # small TTL to stay responsive to admin changes
_settings_cache = {}  # key -> (expires_at, found, raw_value, value_type)
_settings_cache_lock = threading.RLock()


def _to_bool(v) -> bool:
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "on")


def _cache_get(key: str):
    now = time.time()
    with _settings_cache_lock:
        item = _settings_cache.get(key)
        if not item:
            return None
        expires_at, found, raw, vtype = item
        if expires_at <= now:
            try:
                del _settings_cache[key]
            except Exception:
                pass
            return None
        return (found, raw, vtype)


def _cache_set(key: str, found: bool, raw_value, value_type: str):
    expires_at = time.time() + float(_SETTINGS_CACHE_TTL_SEC)
    with _settings_cache_lock:
        _settings_cache[key] = (expires_at, bool(found), raw_value, (value_type or "").lower())


def _cache_invalidate(key: str):
    with _settings_cache_lock:
        try:
            del _settings_cache[key]
        except Exception:
            pass


def get_setting(key: str, default=None, cast=None):
    """
    Fetch a setting from llm_app_settings. DB is the only truth.
    Uses a small in-process TTL cache to avoid hitting MySQL on every read.
    - cast can be: int, float, bool, "json", or None (string)
    """
    cached = _cache_get(key)
    if cached is None:
        conn = mysql_conn()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT `value`, `value_type` FROM llm_app_settings WHERE `key`=%s", (key,))
            row = cur.fetchone()
            if not row:
                _cache_set(key, found=False, raw_value=None, value_type="")
                return default

            raw = row["value"]
            vtype = (row["value_type"] or "").lower()
            _cache_set(key, found=True, raw_value=raw, value_type=vtype)
        finally:
            try:
                cur.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
    else:
        found, raw, vtype = cached
        if not found:
            return default

    # If caller provided a cast, it wins.
    if cast is not None:
        if cast is int:
            return int(raw)
        if cast is float:
            return float(raw)
        if cast is bool:
            return _to_bool(raw)
        if cast == "json":
            return json.loads(raw)
        return str(raw)

    # Otherwise use DB value_type.
    if vtype == "int":
        return int(raw)
    if vtype == "float":
        return float(raw)
    if vtype == "bool":
        return _to_bool(raw)
    if vtype == "json":
        return json.loads(raw)
    return str(raw)


def set_setting(key: str, value, value_type: str = None, updated_by_user_id=None):
    """
    Upsert a setting into llm_app_settings.
    If value_type is None, infer a sane one.
    Cache is write-through (so admin changes reflect immediately).
    """
    if value_type is None:
        if isinstance(value, bool):
            value_type = "bool"
            value = "1" if value else "0"
        elif isinstance(value, int):
            value_type = "int"
            value = str(value)
        elif isinstance(value, float):
            value_type = "float"
            value = str(value)
        else:
            value_type = "string"
            value = str(value)
    else:
        value_type = str(value_type).lower()
        if value_type == "bool":
            value = "1" if _to_bool(value) else "0"
        else:
            value = str(value)

    conn = mysql_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO llm_app_settings (`key`,`value`,`value_type`,`updated_by_user_id`)
            VALUES (%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              `value`=VALUES(`value`),
              `value_type`=VALUES(`value_type`),
              `updated_by_user_id`=VALUES(`updated_by_user_id`),
              `updated_at`=CURRENT_TIMESTAMP
            """,
            (key, value, value_type, updated_by_user_id),
        )
        conn.commit()
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

    # Write-through cache update so reads reflect immediately.
    _cache_set(key, found=True, raw_value=value, value_type=value_type)


def get_all_settings_rows():
    """
    Return raw settings rows exactly as stored in llm_app_settings.
    Values stay in their persisted DB form so backups reflect saved state exactly.
    """
    conn = mysql_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            "SELECT `key`, `value`, `value_type` FROM llm_app_settings ORDER BY `key` ASC"
        )
        rows = cur.fetchall() or []
        return [
            {
                "key": row["key"],
                "value": row["value"],
                "value_type": (row.get("value_type") or "string").lower(),
            }
            for row in rows
        ]
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def _get_required_setting_text(key: str) -> str:
    value = get_setting(key)
    if value is None:
        raise RuntimeError(f"Missing required setting: {key}")
    value = str(value).strip()
    if not value:
        raise RuntimeError(f"Missing required setting: {key}")
    return value


def _get_required_setting_int(key: str, minimum: int = None) -> int:
    raw = _get_required_setting_text(key)
    try:
        value = int(raw)
    except Exception as exc:
        raise RuntimeError(f"Invalid required setting: {key}") from exc
    if minimum is not None and value < int(minimum):
        raise RuntimeError(f"Invalid required setting: {key}")
    return value


def _get_required_setting_bool(key: str) -> bool:
    raw = _get_required_setting_text(key).lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise RuntimeError(f"Invalid required setting: {key}")


def get_password_policy():
    """
    Returns normalized password policy dict from DB.
    Matches your frontend shape and existing server-side expectations.
    """
    level = _get_required_setting_text("security.password_policy.level").lower()
    if level not in ("basic", "moderate", "strong", "custom"):
        raise RuntimeError("Invalid required setting: security.password_policy.level")

    # For now, we always return explicit flags (even for non-custom),
    # because your UI expects concrete values.
    min_length = _get_required_setting_int("security.password_policy.min_length", minimum=6)
    require_upper = _get_required_setting_bool("security.password_policy.require_upper")
    require_lower = _get_required_setting_bool("security.password_policy.require_lower")
    require_digit = _get_required_setting_bool("security.password_policy.require_digit")
    require_special = _get_required_setting_bool("security.password_policy.require_special")

    return {
        "level": level,
        "min_length": min_length,
        "require_upper": require_upper,
        "require_lower": require_lower,
        "require_digit": require_digit,
        "require_special": require_special,
    }
