import json
import os
import secrets
from urllib.parse import urlparse


BOOTSTRAP_CONFIG_ENV_VAR = "LLM_CONTROLLER_BOOTSTRAP_CONFIG"
BOOTSTRAP_CONFIG_FILENAME = "bootstrap_config.json"
APP_SECRET_FILENAME = "flask_secret.key"
CORS_ALLOWED_ORIGINS_KEY = "cors-allowed-origins"

DEFAULT_BOOTSTRAP_CONFIG = {
    "app_host": "127.0.0.1",
    "app_port": 5000,
    "db_host": "localhost",
    "db_port": 3306,
    "db_name": "llmcontroller",
    "db_user": "llmcontroller",
    "db_password": "",
    CORS_ALLOWED_ORIGINS_KEY: [],
    "setup_complete": False,
}

LOCAL_BIND_HOSTS = {"127.0.0.1", "localhost"}
WILDCARD_BIND_HOSTS = {"0.0.0.0", "::", "[::]"}


def resolve_bootstrap_config_path() -> str:
    override = (os.getenv(BOOTSTRAP_CONFIG_ENV_VAR) or "").strip()
    if override:
        return os.path.abspath(override)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, BOOTSTRAP_CONFIG_FILENAME)


def resolve_secret_key_path() -> str:
    return os.path.join(os.path.dirname(resolve_bootstrap_config_path()), APP_SECRET_FILENAME)


def _coerce_port(value, default: int) -> int:
    try:
        port = int(value)
        if 1 <= port <= 65535:
            return port
    except Exception:
        pass
    return int(default)


def _coerce_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(default)


def _coerce_string_list(value):
    if isinstance(value, (list, tuple, set)):
        items = value
    elif isinstance(value, str):
        items = value.split(",")
    else:
        items = []
    return [str(item or "").strip() for item in items if str(item or "").strip()]


def _first_nonempty_config_value(raw, *keys):
    if not isinstance(raw, dict):
        return None

    for key in keys:
        if key not in raw:
            continue

        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value

    return None


def _collapse_duplicate_origin_scheme(origin: str) -> str:
    value = str(origin or "").strip()
    while True:
        lowered = value.lower()
        if lowered.startswith("http://http://"):
            value = "http://" + value[len("http://http://"):]
            continue
        if lowered.startswith("https://https://"):
            value = "https://" + value[len("https://https://"):]
            continue
        if lowered.startswith("http://https://"):
            value = "https://" + value[len("http://https://"):]
            continue
        if lowered.startswith("https://http://"):
            value = "http://" + value[len("https://http://"):]
            continue
        return value


def _safe_parsed_port(parsed):
    try:
        return parsed.port
    except Exception:
        return None


def _format_origin_netloc(host: str, port=None) -> str:
    host_value = str(host or "").strip().strip("[]")
    if not host_value:
        return ""

    if ":" in host_value and not host_value.startswith("["):
        host_value = f"[{host_value}]"

    if port is None:
        return host_value
    return f"{host_value}:{int(port)}"


def normalize_app_host(value, default: str = "") -> str:
    raw = str(value or "").strip()
    if not raw:
        return str(default or "").strip()
    if any(ch.isspace() for ch in raw) or any(marker in raw for marker in ("/", "?", "#", "@")):
        return str(default or "").strip()

    candidate = raw
    if "://" not in candidate:
        candidate = f"http://{candidate.lstrip('/')}"

    parsed = urlparse(candidate)
    host = str(parsed.hostname or "").strip()

    if not host:
        fallback = raw.strip().rstrip("/")
        if "@" in fallback:
            fallback = fallback.rsplit("@", 1)[-1]
        fallback = fallback.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        if fallback.startswith("[") and "]" in fallback:
            host = fallback[1:fallback.index("]")]
        elif fallback.count(":") == 1:
            host = fallback.split(":", 1)[0]
        else:
            host = fallback

    host = str(host or "").strip().strip("[]")
    return host or str(default or "").strip()


def normalize_cors_origin(value, default_port=None) -> str:
    origin = _collapse_duplicate_origin_scheme(str(value or "").strip().rstrip("/"))
    if not origin:
        return ""

    had_scheme = "://" in origin
    if not had_scheme:
        origin = f"http://{origin}"

    parsed = urlparse(origin)
    scheme = str(parsed.scheme or "").strip().lower()
    host = str(parsed.hostname or "").strip()
    if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        return ""

    port = _safe_parsed_port(parsed)
    netloc = _format_origin_netloc(host, port)
    if not netloc:
        return ""
    return f"{scheme}://{netloc}"


def derive_cors_allowed_origins(app_host=None, app_port=None, extra_origins=None):
    host = normalize_app_host(app_host, DEFAULT_BOOTSTRAP_CONFIG["app_host"]) or DEFAULT_BOOTSTRAP_CONFIG["app_host"]
    candidates = []

    if host in WILDCARD_BIND_HOSTS or host in LOCAL_BIND_HOSTS:
        candidates.extend(
            [
                "http://127.0.0.1",
                "http://localhost",
            ]
        )
    else:
        candidates.append(f"http://{host}")

    candidates.extend(_coerce_string_list(extra_origins))

    normalized = []
    seen = set()
    for candidate in candidates:
        origin = normalize_cors_origin(candidate)
        if not origin or origin in seen:
            continue
        seen.add(origin)
        normalized.append(origin)
    return normalized


def _get_raw_cors_allowed_origins(raw, app_port):
    candidates = []
    if not isinstance(raw, dict):
        return candidates

    if raw.get(CORS_ALLOWED_ORIGINS_KEY) is not None:
        candidates.extend(_coerce_string_list(raw.get(CORS_ALLOWED_ORIGINS_KEY)))
    elif raw.get("cors_allowed_origins") is not None:
        candidates.extend(_coerce_string_list(raw.get("cors_allowed_origins")))

    for legacy_key in ("public_app_origin", "public_origin", "public_cors"):
        legacy_value = raw.get(legacy_key)
        if legacy_value:
            candidates.append(str(legacy_value))

    normalized = []
    seen = set()
    for candidate in candidates:
        origin = normalize_cors_origin(candidate)
        if not origin or origin in seen:
            continue
        seen.add(origin)
        normalized.append(origin)
    return normalized


def normalize_bootstrap_config(data=None) -> dict:
    raw = data if isinstance(data, dict) else {}
    raw_app_host = _first_nonempty_config_value(raw, "app_host", "HOST")
    app_host = normalize_app_host(raw_app_host, DEFAULT_BOOTSTRAP_CONFIG["app_host"]) or DEFAULT_BOOTSTRAP_CONFIG["app_host"]
    raw_app_port = _first_nonempty_config_value(raw, "app_port", "PORT")
    app_port = _coerce_port(raw_app_port, DEFAULT_BOOTSTRAP_CONFIG["app_port"])
    raw_cors_allowed_origins = _get_raw_cors_allowed_origins(raw, app_port)
    cors_allowed_origins = derive_cors_allowed_origins(
        app_host,
        app_port,
        extra_origins=raw_cors_allowed_origins,
    )
    return {
        "app_host": app_host,
        "app_port": app_port,
        "db_host": str(raw.get("db_host") or DEFAULT_BOOTSTRAP_CONFIG["db_host"]).strip()
        or DEFAULT_BOOTSTRAP_CONFIG["db_host"],
        "db_port": _coerce_port(raw.get("db_port"), DEFAULT_BOOTSTRAP_CONFIG["db_port"]),
        "db_name": str(raw.get("db_name") or DEFAULT_BOOTSTRAP_CONFIG["db_name"]).strip()
        or DEFAULT_BOOTSTRAP_CONFIG["db_name"],
        "db_user": str(raw.get("db_user") or DEFAULT_BOOTSTRAP_CONFIG["db_user"]).strip()
        or DEFAULT_BOOTSTRAP_CONFIG["db_user"],
        "db_password": str(raw.get("db_password") or ""),
        CORS_ALLOWED_ORIGINS_KEY: cors_allowed_origins,
        "setup_complete": _coerce_bool(raw.get("setup_complete"), False),
    }


def load_bootstrap_config():
    path = resolve_bootstrap_config_path()
    if not os.path.isfile(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return None

    return normalize_bootstrap_config(data)


def get_bootstrap_config() -> dict:
    loaded = load_bootstrap_config()
    if loaded is None:
        return normalize_bootstrap_config()
    return loaded


def save_bootstrap_config(data) -> dict:
    normalized = normalize_bootstrap_config(data)
    path = resolve_bootstrap_config_path()
    parent_dir = os.path.dirname(path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(normalized, handle, indent=2)
        handle.write("\n")

    return normalized


def is_setup_complete(config=None) -> bool:
    current = load_bootstrap_config() if config is None else normalize_bootstrap_config(config)
    if current is None:
        return False
    return bool(current.get("setup_complete"))


def is_installer_mode(config=None) -> bool:
    return not is_setup_complete(config)


def get_app_bind(config=None):
    current = get_bootstrap_config() if config is None else normalize_bootstrap_config(config)
    return current["app_host"], current["app_port"]


def get_cors_allowed_origins(config=None):
    current = get_bootstrap_config() if config is None else normalize_bootstrap_config(config)
    return list(current.get(CORS_ALLOWED_ORIGINS_KEY) or [])


def get_socketio_cors_allowed_origins(config=None):
    current = get_bootstrap_config() if config is None else normalize_bootstrap_config(config)
    return list(current.get(CORS_ALLOWED_ORIGINS_KEY) or [])


def get_or_create_secret_key() -> str:
    path = resolve_secret_key_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            existing = handle.read().strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass
    except Exception:
        pass

    secret_key = secrets.token_urlsafe(48)
    parent_dir = os.path.dirname(path)
    try:
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(secret_key)
            handle.write("\n")
    except Exception:
        return secret_key
    return secret_key
